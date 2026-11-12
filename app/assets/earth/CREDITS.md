# Earth imagery and countries for the 3D globe

- `earth-day.jpg`: NASA Visible Earth, Blue Marble: Next Generation (NASA Goddard Space Flight
  Center), cloud-free land and ocean composite. Public domain. Taken from
  <https://commons.wikimedia.org/wiki/File:Whole_world_-_land_and_oceans_12000.jpg> and resized to
  3072 × 1536.
- `earth-night.jpg`: NASA Earth Observatory, Black Marble 2012 (Suomi NPP VIIRS) night lights.
  Public domain. Taken from <https://commons.wikimedia.org/wiki/File:The_earth_at_night.jpg>,
  converted to greyscale and resized to 2048 × 1024.
- `countries.json`: Natural Earth 1:110m admin-0 countries (public domain), outlines rounded to
  0.01°, with Natural Earth's population and GDP estimates and their years.

`manifest.json` records each file's source, the hash of what was downloaded, the hash of the bundled
file and how it was transformed. Nothing here is fetched while HAYAT runs.
