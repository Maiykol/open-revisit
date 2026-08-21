# Offline map reference

`natural_earth_europe.geojson` is a clipped and simplified WGS84 extract of
Natural Earth’s 1:110 million Admin 0 countries layer. It is used only as
offline geographic context for the Europe figure; no live basemap is fetched.

Source: [Natural Earth 1:110m cultural vectors](https://www.naturalearthdata.com/downloads/110m-cultural-vectors/).
Natural Earth vector data is [public domain](https://www.naturalearthdata.com/about/terms-of-use/).
The checked-in extract was generated from the `naturalearth_lowres` copy
distributed with GeoPandas/pyogrio, clipped to longitude -27..33 and latitude
34..72, then simplified at 0.03 degrees while preserving topology.
