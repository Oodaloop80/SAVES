#!/usr/bin/env python3
"""One-off diagnostic: dump how XDA (or any site) structures its in-article images,
so we can see why trafilatura drops them. Prints JSON; paste the output back.

    python scripts/diag_imgs.py
    python scripts/diag_imgs.py "https://some-other-article-url"
"""
import asyncio
import json
import sys

from playwright.async_api import async_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else \
    "https://www.xda-developers.com/most-people-ollama-llama-cpp-local-llms-tool-serious/"


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        pg = await b.new_page()
        await pg.goto(URL, wait_until="networkidle")
        # scroll to load lazy images, then settle
        await pg.evaluate(
            "async () => { for (let y = 0; y < document.body.scrollHeight; y += 600) "
            "{ window.scrollTo(0, y); await new Promise(r => setTimeout(r, 80)); } "
            "window.scrollTo(0, 0); }"
        )
        await pg.wait_for_timeout(2500)
        data = await pg.evaluate(r"""() => {
            const imgs = [...document.querySelectorAll('img')].filter(i => {
                const s = i.currentSrc || i.src || '';
                return /wp-content|uploads|xdaimages/i.test(s) && (i.naturalWidth || i.width) > 300;
            });
            return {
                content_image_count: imgs.length,
                samples: imgs.slice(0, 3).map(i => {
                    const chain = [];
                    let e = i;
                    for (let k = 0; k < 8 && e; k++, e = e.parentElement) {
                        const cls = e.className ? '.' + String(e.className).trim().split(/\s+/).slice(0, 3).join('.') : '';
                        chain.push(e.tagName.toLowerCase() + cls);
                    }
                    const fig = i.closest('figure') || i.parentElement;
                    return {
                        src: (i.currentSrc || i.src || '').slice(0, 130),
                        ancestor_chain: chain,
                        nearest_figure_or_parent_html: (fig.outerHTML || '').slice(0, 600),
                    };
                }),
            };
        }""")
        print(json.dumps(data, indent=2))
        await b.close()


asyncio.run(main())
