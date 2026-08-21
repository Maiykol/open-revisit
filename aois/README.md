# AOI centroid source

The 20 city-centre coordinates in `centroids.csv` come from the GeoNames
`cities15000` export downloaded on 2026-08-21. GeoNames defines the latitude and
longitude fields as decimal degrees in WGS84. The source dataset and field
documentation are available from:

- <https://download.geonames.org/export/dump/cities15000.zip>
- <https://download.geonames.org/export/dump/readme.txt>

The selected GeoNames IDs are: Hamburg 2911298, Berlin 2950159, Munich 2867714,
London 2643743, Dublin 2964574, Amsterdam 2759794, Paris 2988507, Marseille
2995469, Madrid 3117735, Lisbon 2267057, Rome 3169070, Athens 264371, Zürich
2657896, Innsbruck 2775220, Warsaw 756135, Copenhagen 2618425, Stockholm
2673730, Oslo 3143244, Tromsø 3133895, and Reykjavík 3413829.

These points are used only as reproducible centres for constructing equal-area
20 km × 20 km AOIs; they are not administrative centroids or city boundaries.
GeoNames data is licensed under CC BY 4.0; attribution: GeoNames.
