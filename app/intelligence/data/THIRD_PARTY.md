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
