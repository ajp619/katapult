-- Convert standard ```mermaid code blocks to <pre class="mermaid"> for CDN mermaid.js
function CodeBlock(el)
  for _, c in ipairs(el.attr.classes) do
    if c == "mermaid" then
      return pandoc.RawBlock("html",
        "<pre class=\"mermaid\">\n" .. el.text .. "\n</pre>")
    end
  end
  return el
end