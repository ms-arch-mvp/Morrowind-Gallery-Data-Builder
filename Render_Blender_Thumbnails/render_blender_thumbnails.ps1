param(
    [Parameter(Mandatory = $true)][string]$input_dir
)

$ErrorActionPreference = 'Stop'

# ---- settings from config.ini (next to this script) ---------------------
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Read-IniSettings($path) {
    $h = @{}
    if (-not (Test-Path -LiteralPath $path)) { return $h }
    foreach ($line in Get-Content -LiteralPath $path) {
        $t = $line.Trim()
        if ($t -eq '' -or $t.StartsWith('#') -or $t.StartsWith(';') -or $t.StartsWith('[')) { continue }
        $i = $t.IndexOf('=')
        if ($i -lt 1) { continue }
        $h[$t.Substring(0, $i).Trim()] = $t.Substring($i + 1).Trim()
    }
    return $h
}
function IniStr($cfg, $key, $def) { if ($cfg.ContainsKey($key) -and $cfg[$key] -ne '') { $cfg[$key] } else { $def } }
function IniInt($cfg, $key, $def) { $v = IniStr $cfg $key $null; if ($null -ne $v) { try { [int]$v } catch { $def } } else { $def } }
function IniBool($cfg, $key, $def) { $v = IniStr $cfg $key $null; if ($null -ne $v) { @('1', 'true', 'yes', 'on') -contains $v.ToLower() } else { $def } }
function IniList($cfg, $key, $def) { $v = IniStr $cfg $key $null; if ($null -ne $v) { @($v -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' }) } else { $def } }

$cfg = Read-IniSettings (Join-Path $scriptDir 'config.ini')
$paths = Read-IniSettings (Join-Path (Split-Path $scriptDir -Parent) 'Shared\paths.ini')

$blender     = IniStr  $paths 'blender' 'C:\Program Files\Blender Foundation\Blender 5.0\blender.exe'
$run_oxipng  = IniBool $cfg 'run_oxipng' $true
$oxipng_path = Join-Path (Split-Path $scriptDir -Parent) 'Shared\oxipng.exe'

$workers    = IniInt $cfg 'workers' 8
if ($workers -le 0) { $workers = [Environment]::ProcessorCount }
$format     = IniStr $cfg 'format' 'PNG'
$resolution = IniInt $cfg 'resolution' 1024

$lowercase_output = IniBool $cfg 'lowercase_output' $true
$flat_output      = IniBool $cfg 'flat_output' $false
$force_blend_only = IniBool $cfg 'force_blend_only' $false

$global_rotation                 = [float](IniStr $cfg 'global_rotation' '0')
$render_only_rotation_exceptions = IniBool $cfg 'render_only_rotation_exceptions' $false
$render_vertex_normals           = IniBool $cfg 'render_vertex_normals' $false
$cull_backfaces                  = IniBool $cfg 'cull_backfaces' $true
$auto_set_emissive_color         = IniBool $cfg 'auto_set_emissive_color' $true
$emissive_exceptions             = IniList $cfg 'emissive_exceptions' @('ray')

$subfolder_filter  = IniList $cfg 'subfolder_filter' @()
$body_part_folders = IniList $cfg 'body_part_folders' @('b')
$creature_folders  = IniList $cfg 'creature_folders' @('cr', 'r', 'wolf')
$npc_folders       = IniList $cfg 'npc_folders' @('npc')
$hair_exceptions   = IniList $cfg 'hair_exceptions' @('_hair', '_hr_')

$nif_use_existing_materials     = IniBool $cfg 'nif_use_existing_materials' $true
$nif_ignore_collision_nodes     = IniBool $cfg 'nif_ignore_collision_nodes' $true
$nif_ignore_animations          = IniBool $cfg 'nif_ignore_animations' $false
$nif_ignore_armatures           = IniBool $cfg 'nif_ignore_armatures' $false
$nif_ignore_billboard_nodes     = IniBool $cfg 'nif_ignore_billboard_nodes' $true
$nif_ignore_emissive_color      = IniBool $cfg 'nif_ignore_emissive_color' $false
$nif_ignore_tri_shadow          = IniBool $cfg 'nif_ignore_tri_shadow' $true
$nif_ignore_nodes               = IniStr  $cfg 'nif_ignore_nodes' 'Lightning'
$nif_ignore_nodes_under_switches = IniStr $cfg 'nif_ignore_nodes_under_switches' 'OFF, HARVESTED, Closed'
$nif_filter_best_lod            = IniBool $cfg 'nif_filter_best_lod' $true

$output_dir = Join-Path $scriptDir 'output'

if (-not $input_dir) {
    Write-Host "Error: No InputDir provided." -ForegroundColor Red
    exit 1
}

# --- Check prerequisites (see the README) ---
if (-not (Test-Path -LiteralPath $blender)) {
    Write-Host "Missing prerequisite: Blender not found at '$blender'." -ForegroundColor Red
    Write-Host "Edit the 'blender' path in config.ini (see the README)." -ForegroundColor Red
    exit 1
}

# Add ID for MessageBox and DPI awareness
Add-Type -AssemblyName System.Windows.Forms
$dpiSignature = @"
[DllImport("user32.dll")]
public static extern bool SetProcessDPIAware();
"@
Add-Type -MemberDefinition $dpiSignature -Name "DPIUtils" -Namespace "Win32"

# Normalize paths
$input_dir = [System.IO.Path]::GetFullPath($input_dir).TrimEnd('\', '/')
$output_dir = [System.IO.Path]::GetFullPath($output_dir).TrimEnd('\', '/')
$script_path = Join-Path $PSScriptRoot "render_blender_thumbnails.py"
# Root that thumbnails are written under: the output folder itself when flat,
# otherwise an output\meshes tree that mirrors the source folder structure.
$render_root = if ($flat_output) { $output_dir } else { Join-Path $output_dir "meshes" }
$logs_dir = Join-Path $output_dir "logs"
$failed_log_path = Join-Path $logs_dir "failed.txt"
$empty_log_path = Join-Path $logs_dir "empty.txt"

# Parse rotation exceptions
$exceptions_file = Join-Path $PSScriptRoot "rotation_exceptions.txt"
$exceptions = @()

if (Test-Path $exceptions_file) {
    $lines = Get-Content -Path $exceptions_file
    $current_rotation = $null
    foreach ($line in $lines) {
        $line = $line.Trim()
        if ($line -eq "" -or $line.StartsWith("#")) {
            continue
        }
        
        # Check for group header like "Rotate 180:"
        if ($line -match "(?i)^Rotate\s+(\d+)\s*:") {
            $current_rotation = [float]$Matches[1]
            continue
        }
        
        if ($null -ne $current_rotation) {
            # Normalize exception path
            $norm_path = $line.Replace("/", "\").ToLower()
            $norm_path = [regex]::Replace($norm_path, '\\+', '\').Trim('\')
            
            # Strip .nif/.blend extension if present at the end
            if ($norm_path.EndsWith(".nif")) {
                $norm_path = $norm_path.Substring(0, $norm_path.Length - 4)
            } elseif ($norm_path.EndsWith(".blend")) {
                $norm_path = $norm_path.Substring(0, $norm_path.Length - 6)
            }
            
            $dir_part = ""
            $file_part = ""
            $last_slash = $norm_path.LastIndexOf("\")
            if ($last_slash -ge 0) {
                $dir_part = $norm_path.Substring(0, $last_slash)
                $file_part = $norm_path.Substring($last_slash + 1)
            } else {
                $file_part = $norm_path
            }
            
            $exceptions += [PSCustomObject]@{
                Directory = $dir_part
                Filename  = $file_part
                Rotation  = $current_rotation
            }
        }
    }
}

Write-Host "=========================================="
Write-Host "Thumbnails Generation started at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host "Input Directory:  $input_dir"
Write-Host "Output Directory: $output_dir"
Write-Host "Auto-set emissive: $auto_set_emissive_color"
Write-Host "Emissive exceptions: $($emissive_exceptions -join ',')"
Write-Host "Render only rotation exceptions: $render_only_rotation_exceptions"
Write-Host "=========================================="

# Ensure output directory exists
if (-not (Test-Path $output_dir)) {
    New-Item -ItemType Directory -Path $output_dir | Out-Null
}

# Determine the source type for this run. A run is only ever .nif OR .blend, never both.
# force_blend_only always uses .blend; otherwise .nif is preferred, falling back to .blend.
if ($force_blend_only) {
    $source_ext = 'blend'
    Write-Host "force_blend_only enabled: processing .blend files only."
} else {
    $nif_count = @(Get-ChildItem -Path $input_dir -Filter '*.nif' -Recurse -File).Count
    if ($nif_count -gt 0) {
        $source_ext = 'nif'
    } else {
        $source_ext = 'blend'
        Write-Host "No .nif files found in $input_dir - falling back to .blend files." -ForegroundColor Yellow
    }
}

# Find all source files recursively
$files = @(Get-ChildItem -Path $input_dir -Filter "*.$source_ext" -Recurse -File | Sort-Object FullName)

if ($files.Count -eq 0) {
    Write-Host "No .$source_ext files found in $input_dir" -ForegroundColor Yellow
    exit 0
}

Write-Host "Found $($files.Count) .$source_ext files. Processing in parallel..."
Write-Host "Logs directory: $logs_dir"

$processes = New-Object System.Collections.ArrayList
$jobs = New-Object System.Collections.ArrayList
$script:failed_paths = New-Object System.Collections.Generic.List[string]
$script:empty_paths = New-Object System.Collections.Generic.List[string]
$script:oxipng_targets = New-Object System.Collections.Generic.List[string]
$script:success_count = 0
$script:failure_count = 0
$script:empty_count = 0

function Get-StatusPrefix {
    param([pscustomobject]$job, [string]$label)
    return "[{0} of {1}] {2}" -f $job.job_number, $jobs.Count, $label
}

function Ensure-LogsDir {
    if (-not (Test-Path $logs_dir)) {
        New-Item -ItemType Directory -Path $logs_dir | Out-Null
    }
}

function Write-PathLog {
    param(
        [string]$path,
        [System.Collections.Generic.List[string]]$entries
    )

    if ($entries.Count -gt 0) {
        Ensure-LogsDir
        Set-Content -Path $path -Value $entries -Encoding UTF8
    } elseif (Test-Path $path) {
        Remove-Item -LiteralPath $path -Force
    }
}

function Finalize-CompletedProcesses {
    for ($i = $processes.Count - 1; $i -ge 0; $i--) {
        $job = $processes[$i]
        if (-not $job.process.HasExited) {
            continue
        }

        $processes.RemoveAt($i)

        $output_exists = Test-Path $job.output_path
        $output_length = if ($output_exists) { (Get-Item $job.output_path).Length } else { 0 }
        $is_empty_render = $output_exists -and ($output_length -eq 0)
        $is_success = $output_exists -and ($output_length -gt 0)

        if ($is_success) {
            $script:success_count++
            [void]$script:oxipng_targets.Add($job.output_path)
        }
        elseif ($is_empty_render) {
            $script:empty_count++
            [void]$script:empty_paths.Add($job.relative_path)
        }
        else {
            $script:failure_count++
            [void]$script:failed_paths.Add($job.relative_path)
        }

        # Blender's per-render output was redirected to temp files; drop them.
        Remove-Item -LiteralPath $job.log_out, $job.log_err -Force -ErrorAction SilentlyContinue

        # One updating progress line instead of a line (and Blender dump) per file.
        $done = $script:success_count + $script:empty_count + $script:failure_count
        Write-Host -NoNewline ("`r  Rendered {0}/{1}   ok {2}  empty {3}  failed {4}    " -f `
            $done, $jobs.Count, $script:success_count, $script:empty_count, $script:failure_count)
    }
}

foreach ($f in $files) {
    # Calculate the relative path from input_dir to the file
    # GetRelativePath is only in PS Core, so we use string replacement for PS 5.1 compatibility
    $rel_path = $f.FullName.Replace($input_dir, "").TrimStart("\")
    
    # Check rotation exceptions
    $norm_rel = $rel_path.Replace("/", "\").ToLower()
    $norm_rel = [regex]::Replace($norm_rel, '\\+', '\').Trim('\')
    
    if ($norm_rel.EndsWith(".$source_ext")) {
        $norm_rel = $norm_rel.Substring(0, $norm_rel.Length - ($source_ext.Length + 1))
    }
    
    $job_dir = ""
    $job_file = ""
    $last_slash = $norm_rel.LastIndexOf("\")
    if ($last_slash -ge 0) {
        $job_dir = $norm_rel.Substring(0, $last_slash)
        $job_file = $norm_rel.Substring($last_slash + 1)
    } else {
        $job_file = $norm_rel
    }
    
    $matched_rotation = $null
    foreach ($exc in $exceptions) {
        if ($exc.Directory -eq $job_dir -and $job_file.Contains($exc.Filename)) {
            $matched_rotation = $exc.Rotation
        }
    }

    if ($render_only_rotation_exceptions -and $null -eq $matched_rotation) {
        continue
    }
    
    if ($subfolder_filter.Count -gt 0) {
        $parts = $f.FullName -split "[\\\/]"
        $match = $false
        foreach ($folder in $subfolder_filter) {
            if ($parts -contains $folder) {
                $match = $true
                break
            }
        }
        if (-not $match) {
            continue
        }
    }

    $rel_dir = [System.IO.Path]::GetDirectoryName($rel_path)
    $file_name_no_ext = [System.IO.Path]::GetFileNameWithoutExtension($f.FullName)
    $is_hair_exception = $false
    foreach ($pattern in $hair_exceptions) {
        if ($file_name_no_ext -like "*$pattern*") {
            $is_hair_exception = $true
            break
        }
    }
    
    # Calculate target output path
    $out_rel_dir = $rel_dir
    $out_file_name = $file_name_no_ext
    if ($lowercase_output) {
        $out_rel_dir = $out_rel_dir.ToLower()
        $out_file_name = $out_file_name.ToLower()
    }
    if ($flat_output) {
        $target_dir = $render_root
    } else {
        $target_dir = Join-Path $render_root $out_rel_dir
    }
    $file_ext = if ($format -eq 'TGA') { 'tga' } else { 'png' }
    $target_file = Join-Path $target_dir "$out_file_name.$file_ext"

    # Ensure target subdirectory exists
    if (-not (Test-Path $target_dir)) {
        New-Item -ItemType Directory -Path $target_dir | Out-Null
    }

    # Check if the file is in a creature folder
    $is_creature = $false
    if ($creature_folders.Count -gt 0) {
        $parts = $f.FullName -split "[\\\/]"
        foreach ($folder in $creature_folders) {
            if ($parts -contains $folder) {
                $is_creature = $true
                break
            }
        }
    }

    if ($is_hair_exception) {
        $is_creature = $false
    }

    # Check if the file is in an NPC folder
    $is_npc = $false
    if ($npc_folders.Count -gt 0) {
        $parts = $f.FullName -split "[\\\/]"
        foreach ($folder in $npc_folders) {
            if ($parts -contains $folder) {
                $is_npc = $true
                break
            }
        }
    }

    if ($is_hair_exception) {
        $is_npc = $false
    }

    # Check if the file is in a body part folder
    $is_body_part = $false
    if ($body_part_folders.Count -gt 0) {
        $parts = $f.FullName -split "[\\\/]"
        foreach ($folder in $body_part_folders) {
            if ($parts -contains $folder) {
                $is_body_part = $true
                break
            }
        }
    }

    if ($is_hair_exception) {
        $is_body_part = $false
    }

    $job_info = [PSCustomObject]@{
        job_number = $jobs.Count + 1
        relative_path = $rel_path
        source_path = $f.FullName
        output_path = $target_file
        is_creature = $is_creature
        is_npc = $is_npc
        is_body_part = $is_body_part
        matched_rotation = $matched_rotation
        process = $null
        log_out = Join-Path $env:TEMP ("rbt_" + [guid]::NewGuid().ToString('N') + ".out.log")
        log_err = Join-Path $env:TEMP ("rbt_" + [guid]::NewGuid().ToString('N') + ".err.log")
    }
    [void]$jobs.Add($job_info)
}

foreach ($job in $jobs) {
    # Remove prior outputs so success/failure is based on this run only.
    if (Test-Path $job.output_path) {
        Remove-Item -LiteralPath $job.output_path -Force
    }

    # Run Blender in background to process the NIF
    # Passing Input Path and Output Path as arguments
    $rot = if ($null -ne $job.matched_rotation) { $job.matched_rotation } else { $global_rotation }
    $proc_args = "--background --python `"$script_path`" -- `"$($job.source_path)`" `"$($job.output_path)`" --resolution $resolution --rotation $rot --format $format"
    if ($cull_backfaces) {
        $proc_args += " --cull-backfaces"
    }
    if ($job.is_creature) {
        $proc_args += " --is-creature"
    }
    if ($job.is_npc) {
        $proc_args += " --is-npc"
    }
    if ($job.is_body_part) {
        $proc_args += " --is-body-part"
    }
    if ($auto_set_emissive_color) {
        $proc_args += " --auto-set-emissive-color"
    }
    if ($emissive_exceptions -and $emissive_exceptions.Count -gt 0) {
        foreach ($ex in $emissive_exceptions) {
            if ($ex -and $ex.Trim() -ne '') {
                $proc_args += " --emissive-exception `"$ex`""
            }
        }
    }
    if ($render_vertex_normals) {
        $proc_args += " --vertex-normals"
    }

    # Forward NIF import settings (worker ignores these for .blend sources).
    # Order mirrors the importer's own attribute list.
    $proc_args += " --nif-use-existing-materials $nif_use_existing_materials"
    $proc_args += " --nif-ignore-collision-nodes $nif_ignore_collision_nodes"
    $proc_args += " --nif-ignore-animations $nif_ignore_animations"
    $proc_args += " --nif-ignore-armatures $nif_ignore_armatures"
    $proc_args += " --nif-ignore-billboard-nodes $nif_ignore_billboard_nodes"
    $proc_args += " --nif-ignore-emissive-color $nif_ignore_emissive_color"
    $proc_args += " --nif-ignore-tri-shadow $nif_ignore_tri_shadow"
    $proc_args += " --nif-ignore-nodes `"$nif_ignore_nodes`""
    $proc_args += " --nif-ignore-nodes-under-switches `"$nif_ignore_nodes_under_switches`""
    $proc_args += " --nif-filter-best-lod $nif_filter_best_lod"

    $process = Start-Process -FilePath $blender -ArgumentList $proc_args -NoNewWindow -PassThru -RedirectStandardOutput $job.log_out -RedirectStandardError $job.log_err
    $job.process = $process
    [void]$processes.Add($job)

    # Manage parallel jobs
    while (@( $processes | Where-Object { -not $_.process.HasExited } ).Count -ge $workers) {
        Start-Sleep -Milliseconds 500
        Finalize-CompletedProcesses
    }
}

# Wait for all remaining jobs to finish
while (@( $processes | Where-Object { -not $_.process.HasExited } ).Count -gt 0) {
    Start-Sleep -Milliseconds 500
    Finalize-CompletedProcesses
}
Write-Host ""   # end the running progress line

Write-PathLog -Path $failed_log_path -Entries $script:failed_paths
Write-PathLog -Path $empty_log_path -Entries $script:empty_paths

Write-Host "=========================================="
Write-Host "Thumbnail generation complete."
Write-Host "Results saved to $output_dir"
Write-Host "Successful renders: $success_count" -ForegroundColor Green
Write-Host "Failed renders:     $failure_count" -ForegroundColor Red
Write-Host "Empty renders:      $empty_count" -ForegroundColor Yellow
if (($failure_count + $empty_count) -gt 0) {
    Write-Host "Failure logs:       $logs_dir"
}
Write-Host "=========================================="

# Run oxipng compression pass on successfully rendered PNGs only (batched to avoid command-line length limits)
if ($run_oxipng -and $format -eq 'PNG') {
    if (Test-Path $oxipng_path) {
        $targets = $script:oxipng_targets
        if ($targets.Count -gt 0) {
            $batch_size = 200
            $batches = [math]::Ceiling($targets.Count / $batch_size)
            Write-Host "=========================================="
            Write-Host "Running oxipng compression pass on $($targets.Count) file(s) in $batches batch(es)..."
            Write-Host "=========================================="
            for ($b = 0; $b -lt $batches; $b++) {
                $start = $b * $batch_size
                $batch = $targets.GetRange($start, [math]::Min($batch_size, $targets.Count - $start))
                Write-Host "oxipng batch ($($b + 1)/$batches)..."
                & $oxipng_path -o 2 -s --alpha @batch
            }
            Write-Host "=========================================="
            Write-Host "oxipng pass complete."
            Write-Host "=========================================="
        } else {
            Write-Host "oxipng: no successful renders to compress, skipping." -ForegroundColor Yellow
        }
    } else {
        Write-Host "WARNING: oxipng.exe not found in the Shared folder - skipping compression pass." -ForegroundColor Yellow
        Write-Host "         Put oxipng.exe at $oxipng_path (or set run_oxipng = false)." -ForegroundColor Yellow
    }
}

try { [void][Win32.DPIUtils]::SetProcessDPIAware() } catch { }
[System.Windows.Forms.MessageBox]::Show("Thumbnails written to the output folder.", "Morrowind Blender Thumbnail Generator", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Information)

# Open the output folder
if (Test-Path $output_dir) { Invoke-Item $output_dir }