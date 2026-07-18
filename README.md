<h1 align="center">Morrowind Gallery Data Builder</h1>
<p align="center">
  <a href="https://ms-arch.gitbook.io/morrowind-visualisation-project/morrowind-gallery-data-builder/functions">Documentation</a>
</p>
  
* `build_gallery_data.bat` generates filtered JSONs for galleries
<br>Uses tes3conv to generate JSON, then JSON is filtered.
* `webp_thumbnails.bat` converts PNGs into WebP images suitable for galleries.
<br>Thumbnails folder: Low resolution images for gallery
<br>Renders folder: High resolution images for opening
* Works off folder inputs.

### Prerequistes

* [tes3conv](https://github.com/Greatness7/tes3conv) in the same folder (for `build_gallery_data.bat`)
* Python installation
* [IrfanView](https://www.irfanview.com/) (for `webp_thumbnails.bat`)
* [IrfanView Plugins](https://www.irfanview.com/plugins.htm)
