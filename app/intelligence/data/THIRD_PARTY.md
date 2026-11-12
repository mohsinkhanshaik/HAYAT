# Third-party data in this folder

## tallinn_cameras.json

The Tallinn camera list (ids, names, positions, headings and snapshot
addresses) is copied from `config/cctv_sources.tallinn.json` in
[gods-eye-view](https://github.com/bilawalsidhu/gods-eye-view) by Bilawal
Sidhu, commit `591f299`, on 2026-10-11, keeping only the fields HAYAT uses.
That project is released under the MIT licence:

> MIT License
>
> Copyright (c) 2026 Bilawal Sidhu
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all
> copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

The camera frames themselves are public traffic camera images published by the
City of Tallinn at ristmikud.tallinn.ee, and remain under the City's terms.
HAYAT fetches them only when the owner asks to look, with publisher cooldowns,
and does not record them.

## transit_routes.json

The route-id tables for Hamilton HSR, Guelph Transit, Burlington Transit and
Rio de Janeiro's SMTR feeds (internal route keys to the numbers riders know)
are copied from `src/data/transitFeeds.js` and `src/data/rioRouteNames.js` in
[gods-eye-view](https://github.com/bilawalsidhu/gods-eye-view), commit
`6e4da23`, on 2026-11-06, under the MIT licence quoted above. Those tables were
built from each operator's own GTFS `routes.txt`, which is published under the
same terms as the operator's realtime feed (City of Hamilton Open Data Licence,
Open Government Licence – City of Guelph, City of Burlington Open Data Terms of
Use, and CC BY 4.0 for SMTR Rio).

## osm_infrastructure.json (ODbL 1.0)

Data centres (4,352 points), dams (703 points, a selection that is mostly
hydro-power dams) and named military areas (36,448 label points) near a place,
for the `mapped_infrastructure` tool. 2.9 MB.

**© OpenStreetMap contributors**, available under the
[Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/)
(<https://www.openstreetmap.org/copyright>). Dams via
[Open Infrastructure Map](https://openinframap.org/); military area names
distributed by the [Overture Maps Foundation](https://docs.overturemaps.org/attribution/)
(base/land_use release 2026-09-23.1, OSM snapshots 2026-09-06 and 2026-09-12).

Derived from `src/data/local_data/{datacenters,dams,osm_military_names}` in
[gods-eye-view](https://github.com/bilawalsidhu/gods-eye-view), commit
`52187c9`, on 2026-11-08 (`names.json` SHA-256
`74cc07f12c496a622f2f6c5a81922c293bca5eb44e0901c967bbf94e15ee514f`, matching
upstream's `names.sha256`). Those folders are distributed under ODbL 1.0,
separately from that project's MIT code. HAYAT's file is a derived database:
each polygon was reduced to the average of its outer ring, and only the OSM id,
the point, the name and the operator (military: class and area) were kept;
upstream had already removed contact tags, and any name or operator that looks
like an e-mail address, phone number or link is dropped. This derived database
is likewise offered under ODbL 1.0 with the attribution above.

Why bundled rather than fetched live: the live alternative, the public Overpass
API, refuses automated clients (overpass-api.de `robots.txt` disallows `/api/`),
and gods-eye-view itself stopped using public Overpass for the same reason.
