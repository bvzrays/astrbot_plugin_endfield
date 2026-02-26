import os
from astrbot.api.star import Star
from typing import Dict, Any, Optional

class Renderer:
    def __init__(self, res_path: str, plugin: Star):
        self.plugin = plugin
        self.res_path = res_path

    def get_template(self, name: str) -> str:
        path = os.path.join(self.res_path, name)
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return f.read()
        return ""

    async def render_html(self, template_name: str, data: Dict[str, Any], options: Optional[Dict] = None) -> Optional[str]:
        # Adapt Yunzai template to AstrBot
        # Yunzai uses a custom template engine, AstrBot uses Jinja2.
        # We need to minimally adapt the template content if it uses {{if}} {{each}} etc.
        # For now, let's assume we can do some simple regex replacements or just provide the data.
        
        import jinja2
        # Initialize Jinja2 environment explicitly instead of relying on external renderer
        # to ensure local Playwright gets fully rendered HTML text
        self.jinja_env = jinja2.Environment()
        
        tmpl_content = self.get_template(template_name)
        if not tmpl_content:
            return None
            
        # Basic conversion from Yunzai template (art-template) to Jinja2
        # {{if condition}} -> {% if condition %}
        # {{each list item idx}} -> {% for item in list %}
        # {{/if}} -> {% endif %}
        # {{/each}} -> {% endfor %}
        
        import re
        
        # Globally replace $value with item since Yunzai art-template defaults to $value
        adapted = tmpl_content.replace("$value", "item")
        
        def fix_condition(match):
            cond = match.group(1)
            # Replace common JS operators to Python/Jinja operators
            cond = cond.replace("===", "==").replace("!==", "!=")
            cond = cond.replace("&&", "and").replace("||", "or")
            cond = cond.replace("null", "none")
            cond = re.sub(r'!\s*([\w\.]+)', r'not \1', cond)
            cond = cond.replace(".length", "|length")
            return f"{{% if {cond} %}}"
            
        adapted = re.sub(r'\{\{if\s+(.+?)\}\}', fix_condition, adapted)
        adapted = adapted.replace("{{/if}}", "{% endif %}")
        adapted = adapted.replace("{{else}}", "{% else %}")
        
        def fix_elif(match):
            cond = match.group(1)
            cond = cond.replace("===", "==").replace("!==", "!=")
            cond = cond.replace("&&", "and").replace("||", "or")
            cond = cond.replace("null", "none")
            cond = re.sub(r'!\s*([\w\.]+)', r'not \1', cond)
            cond = cond.replace(".length", "|length")
            return f"{{% elif {cond} %}}"
        adapted = re.sub(r'\{\{else if\s+(.+?)\}\}', fix_elif, adapted)
        
        # Replace {{@var}} with {{var|safe}}
        import re
        adapted = re.sub(r'\{\{@\s*([\w\.]+)\s*\}\}', r'{{\1|safe}}', adapted)
        
        # Replace JS operators inside print tags {{ ... }}
        def fix_print(match):
            content = match.group(1)
            content = content.replace("||", "or").replace("&&", "and").replace("null", "none")
            return f"{{{{{content}}}}}"
        adapted = re.sub(r'\{\{([^%\}]+?)\}\}', fix_print, adapted)
        
        # Replace {{each list item idx}} with {% for item in list %}
        # Also handle {{each list item}}
        def replace_each(match):
            inner = match.group(1).strip().split()
            # inner[0] is the list (can have dots), inner[1] is the item var, inner[2] (optional) is index var
            if len(inner) >= 2:
                # We ignore the index variable for now in simplistic Jinja2 translation
                list_var = inner[0]
                item_var = inner[1]
                return f"{{% for {item_var} in {list_var} %}}"
            return "{% for item in " + inner[0] + " %}" # Fallback
            
        adapted = re.sub(r'\{\{\s*each\s+(.+?)\s*\}\}', replace_each, adapted)
        adapted = adapted.replace("{{/each}}", "{% endfor %}")
        
        # Inline CSS: replace <link rel="stylesheet" href="{{_res_path}}xxx.css"> with <style>...</style>
        def inline_css(match):
            css_rel_path = match.group(1)
            css_full_path = os.path.join(self.res_path, css_rel_path)
            if os.path.exists(css_full_path):
                with open(css_full_path, "r", encoding="utf-8") as f:
                    return f"<style>\n{f.read()}\n</style>"
            return ""
        
        # Match {{_res_path}}path/to.css and {{pluResPath}}path/to.css
        adapted = re.sub(r'<link\s+rel="stylesheet"\s+href="\{\{(?:_res_path|pluResPath)\}\}([^"]+\.css)">', inline_css, adapted)
        
        # Inline images: replace src="{{_res_path}}img.png" with base64
        import base64
        import mimetypes
        
        def inline_image(match):
            img_rel_path = match.group(1)
            img_full_path = os.path.join(self.res_path, img_rel_path)
            if os.path.exists(img_full_path):
                mime, _ = mimetypes.guess_type(img_full_path)
                mime = mime or "image/png"
                with open(img_full_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                    return f'src="data:{mime};base64,{b64}"'
            return match.group(0) # Keep original if not found
            
        adapted = re.sub(r'src="\{\{(?:_res_path|pluResPath)\}\}([^"]+\.(?:png|jpg|jpeg|gif|svg|webp))"', inline_image, adapted)
        
        # Also fix up any remaining {{pluResPath}} or {{_res_path}} in inline styles
        def inline_style_bg(match):
            img_rel_path = match.group(1)
            img_full_path = os.path.join(self.res_path, img_rel_path)
            if os.path.exists(img_full_path):
                mime, _ = mimetypes.guess_type(img_full_path)
                mime = mime or "image/png"
                with open(img_full_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                    return f'url(data:{mime};base64,{b64})'
            return match.group(0)
            
        adapted = re.sub(r'url\(\s*[\'"]?\{\{(?:_res_path|pluResPath)\}\}([^)"]+?)[\'"]?\s*\)', inline_style_bg, adapted)
        
        # Clean up stray jinja formatting and set correct absolute file paths
        data["_res_path"] = ""
        data["pluResPath"] = ""
        
        # Render the HTML locally
        try:
            template = self.jinja_env.from_string(adapted)
            html_content = template.render(**data)
        except Exception as e:
            return None
            
        # Use local Playwright rendering (like astrbot_plugin_html_render does)
        # Bypasses the remote T2I engine limits completely
        import uuid
        from playwright.async_api import async_playwright
        
        output_filename = f"endfield_render_{uuid.uuid4().hex[:8]}.png"
        
        # Construct path inside the plugin directory
        # Self.plugin.context will have the base path or we just use res_path's parent
        plugin_dir = os.path.abspath(os.path.join(self.res_path, ".."))
        cache_dir = os.path.join(plugin_dir, "render_cache")
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir, exist_ok=True)
        else:
            import time
            now_ts = time.time()
            for f in os.listdir(cache_dir):
                if f.startswith("endfield_render_") and f.endswith(".png"):
                    ft = os.path.join(cache_dir, f)
                    if os.path.isfile(ft) and now_ts - os.path.getmtime(ft) > 120:
                        try:
                            os.remove(ft)
                        except Exception:
                            pass
            
        output_path = os.path.join(cache_dir, output_filename)
        
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                context = await browser.new_context(
                    device_scale_factor=2,
                    viewport={"width": 850, "height": 800}
                )
                page = await context.new_page()
                await page.set_content(html_content, wait_until="networkidle")
                
                # Expand viewport to full height
                content_h = await page.evaluate("document.body.scrollHeight")
                full_height = max(content_h, 200)
                await page.set_viewport_size({"width": 850, "height": full_height})
                
                # Wait for next animation frame
                await page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
                
                await page.screenshot(path=output_path, full_page=True)
                await browser.close()
                
            return output_path
            
        except ImportError:
            # Fallback if playwright is unexpectedly missing
            return await self.plugin.html_render(adapted, data, options=options)
        except Exception as e:
            return None
