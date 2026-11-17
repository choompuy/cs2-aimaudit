# Third-party licenses

Everything this repository ships is either original code (MIT, see LICENSE)
or covered below. Artwork that the tools download onto the user's machine
(Valve's radar images and weapon / status icons) is never committed here;
its provenance is documented in docs/ASSETS.md instead.

## Lucide

The two "eye" icons in the 2D replay player list (`ui/roundrep.js`) are the
`eye` and `eye-off` icons from Lucide (https://lucide.dev), taken verbatim
from https://github.com/lucide-icons/lucide.

ISC License

Copyright (c) 2026 Lucide Icons and Contributors

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

Lucide's LICENSE file additionally notes that some of its icons (not the
two used here) derive from the Feather project under the MIT License;
the full notice lives at
https://github.com/lucide-icons/lucide/blob/main/LICENSE.

## Tools referenced but not distributed

These are invoked by name on the user's machine and never shipped:

  * Source2Viewer-CLI (https://github.com/ValveResourceFormat/Source2Viewer-CLI)
    — the optional `game` source of tools/fetch_icons.py decompiles icons
    straight out of a locally installed CS2. Its binary is only executed
    when present on PATH (or at $AIMAUDIT_S2V); nothing of it is copied
    into this repository.
