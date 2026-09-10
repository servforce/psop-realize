export function renderMarkdownDocument(markdown) {
  const lines = normalizeMarkdownEntities(stripMarkdownFrontmatter(String(markdown || ""))).replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  for (let index = 0; index < lines.length;) {
    const line = lines[index];
    const trimmed = trimMarkdownLine(line);
    if (isMarkdownBlankLine(line)) {
      index += 1;
      continue;
    }
    if (/^```/.test(trimmed)) {
      const codeLines = [];
      index += 1;
      while (index < lines.length && !/^```/.test(lines[index].trim())) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      blocks.push(`<pre class="markdown-code-block"><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
      continue;
    }
    const heading = trimmed.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const level = Math.min(6, heading[1].length);
      blocks.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      index += 1;
      continue;
    }
    if (isMarkdownTableStart(lines, index)) {
      const tableLines = [];
      while (index < lines.length && isMarkdownTableLine(lines[index])) {
        tableLines.push(lines[index]);
        index += 1;
      }
      blocks.push(renderMarkdownTable(tableLines));
      continue;
    }
    if (isMarkdownOutlineStart(lines, index)) {
      const outlineLines = [];
      while (index < lines.length && !isMarkdownBlankLine(lines[index]) && parseMarkdownOutlineLine(lines[index])) {
        outlineLines.push(lines[index]);
        index += 1;
      }
      blocks.push(renderMarkdownPlainBlock(outlineLines));
      continue;
    }
    if (/^\s*[-*+]\s+/.test(line)) {
      const listLines = [];
      while (index < lines.length && /^\s*[-*+]\s+/.test(lines[index])) {
        listLines.push(lines[index]);
        index += 1;
      }
      if (hasIndentedListLines(listLines)) {
        blocks.push(renderMarkdownPlainBlock(listLines));
        continue;
      }
      const items = listLines.map((item) => item.replace(/^\s*[-*+]\s+/, ""));
      blocks.push(`<ul>${items.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ul>`);
      continue;
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const listLines = [];
      while (index < lines.length && /^\s*\d+[.)]\s+/.test(lines[index])) {
        listLines.push(lines[index]);
        index += 1;
      }
      if (hasIndentedListLines(listLines)) {
        blocks.push(renderMarkdownPlainBlock(listLines));
        continue;
      }
      const items = listLines.map((item) => item.replace(/^\s*\d+[.)]\s+/, ""));
      blocks.push(`<ol>${items.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ol>`);
      continue;
    }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      blocks.push("<hr />");
      index += 1;
      continue;
    }
    const paragraph = [];
    while (index < lines.length && !isMarkdownBlankLine(lines[index]) && !isMarkdownBlockStart(lines, index)) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    blocks.push(`<p>${paragraph.map((item) => renderInlineMarkdown(item)).join("<br />")}</p>`);
  }
  return blocks.join("") || "<div class='empty'>暂无内容。</div>";
}

function stripMarkdownFrontmatter(markdown) {
  if (!markdown.startsWith("---")) return markdown;
  const lines = markdown.replace(/\r\n/g, "\n").split("\n");
  for (let index = 1; index < lines.length; index += 1) {
    if (lines[index].trim() === "---") return lines.slice(index + 1).join("\n").trimStart();
  }
  return markdown;
}

function trimMarkdownLine(line) {
  return normalizeMarkdownEntities(line)
    .replace(/[\u00a0\u2002\u2003\u3000]/g, " ")
    .trim();
}

function isMarkdownBlankLine(line) {
  return trimMarkdownLine(line) === "";
}

function isMarkdownBlockStart(lines, index) {
  const line = lines[index] || "";
  const trimmed = line.trim();
  return /^```/.test(trimmed)
    || /^(#{1,6})\s+/.test(trimmed)
    || isMarkdownTableStart(lines, index)
    || isMarkdownOutlineStart(lines, index)
    || /^\s*[-*+]\s+/.test(line)
    || /^\s*\d+[.)]\s+/.test(line)
    || /^(-{3,}|\*{3,}|_{3,})$/.test(trimmed);
}

function isMarkdownOutlineStart(lines, index) {
  if (!parseMarkdownOutlineLine(lines[index] || "")) return false;
  return Boolean(parseMarkdownOutlineLine(lines[index + 1] || ""));
}

function parseMarkdownOutlineLine(line) {
  const text = String(line || "");
  const numbered = text.match(/^(\s*)(\d+(?:\.\d+)*)(?:[、.．]|\s+)(.+)$/);
  if (numbered) {
    return {
      depth: Math.max(0, numbered[2].split(".").length - 1),
      marker: numbered[2],
      text: numbered[3].trim(),
    };
  }
  const cnNumbered = text.match(/^(\s*)((?:第[一二三四五六七八九十百千万\d]+[章节篇])|(?:[一二三四五六七八九十]+、)|(?:（[一二三四五六七八九十\d]+）))\s*(.+)$/);
  if (cnNumbered) {
    const leadingDepth = Math.floor(cnNumbered[1].replace(/\t/g, "  ").length / 2);
    return {
      depth: Math.max(0, leadingDepth),
      marker: cnNumbered[2],
      text: cnNumbered[3].trim(),
    };
  }
  return null;
}

function renderMarkdownOutline(outlineLines) {
  return `
    <div class="markdown-outline">
      ${outlineLines.map((line) => {
        const item = parseMarkdownOutlineLine(line);
        if (!item) return "";
        const depth = Math.max(0, Math.min(5, item.depth));
        return `
          <div class="markdown-outline-row" style="--outline-depth:${depth}">
            <span class="markdown-outline-marker">${escapeHtml(item.marker)}</span>
            <span class="markdown-outline-text">${renderInlineMarkdown(item.text)}</span>
          </div>
        `;
      }).join("")}
    </div>
  `;
}

function hasIndentedListLines(lines) {
  return lines.some((line) => /^\s{2,}[-*+\d]/.test(line));
}

function renderMarkdownPlainBlock(lines) {
  return `
    <div class="markdown-plain-block">
      ${lines.map((line) => `<div class="markdown-plain-line">${renderInlineMarkdown(line)}</div>`).join("")}
    </div>
  `;
}

function isMarkdownTableStart(lines, index) {
  return isMarkdownTableLine(lines[index]) && isMarkdownSeparatorLine(lines[index + 1] || "");
}

function isMarkdownTableLine(line) {
  const trimmed = String(line || "").trim();
  return trimmed.startsWith("|") && trimmed.endsWith("|") && trimmed.includes("|");
}

function isMarkdownSeparatorLine(line) {
  if (!isMarkdownTableLine(line)) return false;
  return splitMarkdownTableRow(line).every((cell) => /^:?-{3,}:?$/.test(cell.trim()));
}

function splitMarkdownTableRow(line) {
  return String(line || "")
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function renderMarkdownTable(tableLines) {
  const headers = splitMarkdownTableRow(tableLines[0]);
  const rows = tableLines.slice(2).map(splitMarkdownTableRow).filter((row) => row.some(Boolean));
  const keyValue = headers.length === 2 && /字段|项目|名称/.test(headers[0]) && /内容|说明|取值/.test(headers[1]);
  const tableClass = keyValue ? "markdown-table markdown-kv-table" : "markdown-table";
  return `
    <div class="markdown-table-wrap">
      <table class="${tableClass}">
        <thead>
          <tr>${headers.map((header) => `<th>${renderInlineMarkdown(header)}</th>`).join("")}</tr>
        </thead>
        <tbody>
          ${rows.map((row) => renderMarkdownTableRow(row, headers.length, keyValue)).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderMarkdownTableRow(row, columnCount, keyValue) {
  const normalized = [...row];
  while (normalized.length < columnCount) normalized.push("");
  const fieldClass = keyValue ? videoFieldRowClass(normalized[0]) : "";
  const rowClass = fieldClass ? ` class="${fieldClass}"` : "";
  return `<tr${rowClass}>${normalized.slice(0, columnCount).map((cell, index) => {
    const className = keyValue ? (index === 0 ? " class=\"markdown-kv-field\"" : " class=\"markdown-kv-value\"") : "";
    return `<td${className}>${renderMarkdownTableCell(cell, normalized[0], index, keyValue)}</td>`;
  }).join("")}</tr>`;
}

function renderMarkdownTableCell(cell, field, index, keyValue) {
  return renderInlineMarkdown(cell);
}

function videoFieldRowClass(field) {
  const text = String(field || "");
  if (/标准名称|名称/.test(text)) return "markdown-kv-name-row";
  if (/标准编号|编号|标准号/.test(text)) return "markdown-kv-code-row";
  if (/来源|PDF|文件/.test(text)) return "markdown-kv-source-row";
  if (/对象|适用/.test(text)) return "markdown-kv-object-row";
  if (/主题|风险|要求/.test(text)) return "markdown-kv-topic-row";
  return "";
}

function renderInlineMarkdown(value, options = {}) {
  let html = escapeHtml(normalizeMarkdownEntities(value));
  html = html.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)/g, (match, alt, url) => {
    const safeUrl = safeMarkdownUrl(url);
    if (!safeUrl) return match;
    return `<img class="markdown-image" src="${escapeHtml(safeUrl)}" alt="${alt}" loading="lazy" />`;
  });
  html = html.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)/g, (match, text, url) => {
    const safeUrl = safeMarkdownUrl(url);
    if (!safeUrl) return match;
    return `<a href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer">${text}</a>`;
  });
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  if (options.emphasis !== false) {
    html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/__([^_]+)__/g, "<strong>$1</strong>");
    html = html.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  }
  return html;
}

function normalizeMarkdownEntities(value) {
  return String(value ?? "")
    .replace(/&emsp;?/gi, "\u2003")
    .replace(/&#8195;/gi, "\u2003")
    .replace(/&ensp;?/gi, "\u2002")
    .replace(/&#8194;/gi, "\u2002")
    .replace(/&nbsp;?/gi, " ")
    .replace(/&#160;/gi, " ");
}

function safeMarkdownUrl(url) {
  const decoded = String(url || "")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, "\"")
    .trim();
  if (!decoded) return "";
  if (/^(https?:|data:image\/|\/|\.\/|\.\.\/|#)/i.test(decoded)) return decoded;
  return "";
}


function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#039;" }[ch]));
}

function domId(value) {
  return String(value ?? "").replace(/[^A-Za-z0-9_-]+/g, "_") || "item";
}

function compactDetails(items, separator = " · ") {
  return items.filter(Boolean).join(separator);
}
