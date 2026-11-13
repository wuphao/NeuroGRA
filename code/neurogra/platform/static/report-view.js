'use strict';
(() => {
  const fileKinds = {'./data_appendix.md': 'data_appendix', './audit.md': 'audit', './report.json': 'json', './report.md': 'markdown'};

  function inline(parent, text, anchorPrefix, openFile) {
    // Keep report content as text and allow only known internal links.
    const pattern = /(\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\)|<sup><a href="#reference-\d+">\[\d+\]<\/a><\/sup>|<a id="(?:reference-\d+|source-[A-Za-z0-9_-]+)"><\/a>)/g;
    let end = 0;
    for (const match of text.matchAll(pattern)) {
      parent.append(text.slice(end, match.index));
      const value = match[0];
      if (value.startsWith('**')) {
        const strong = document.createElement('strong');
        strong.textContent = value.slice(2, -2);
        parent.append(strong);
      } else if (value.startsWith('<a id=')) {
        const anchor = document.createElement('span');
        anchor.id = anchorPrefix + value.match(/id="([^"]+)"/)[1];
        parent.append(anchor);
      } else if (value.startsWith('<sup>')) {
        const anchor = document.createElement('a');
        anchor.textContent = value.match(/\[\d+\]/)[0];
        anchor.href = '#' + anchorPrefix + value.match(/reference-\d+/)[0];
        const superscript = document.createElement('sup');
        superscript.append(anchor);
        parent.append(superscript);
      } else {
        const [, label, path] = value.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
        const kind = fileKinds[path.split('#')[0]];
        parent.append((kind && openFile(label, kind)) || label);
      }
      end = match.index + value.length;
    }
    parent.append(text.slice(end));
  }

  window.renderPatientReport = (text, {anchorPrefix, openFile}) => {
    const container = document.createElement('div');
    let rows = [];
    function flushTable() {
      if (!rows.length) return;
      const table = document.createElement('table');
      rows.filter(row => !row.every(cell => /^[:\- ]+$/.test(cell))).forEach((values, index) => {
        const row = table.insertRow();
        for (const value of values) {
          const cell = document.createElement(index ? 'td' : 'th');
          if (!index) cell.scope = 'col';
          inline(cell, value, anchorPrefix, openFile);
          row.append(cell);
        }
      });
      const wrapper = document.createElement('div');
      wrapper.className = 'report-table';
      wrapper.append(table);
      container.append(wrapper);
      rows = [];
    }
    for (const line of text.split(/\r?\n/)) {
      if (line.startsWith('|')) {
        rows.push(line.split('|').slice(1, -1).map(value => value.trim()));
        continue;
      }
      flushTable();
      if (!line.trim()) continue;
      const heading = line.match(/^(#{1,4})\s+(.+)/);
      const block = document.createElement(heading ? 'h' + heading[1].length : 'p');
      inline(block, heading ? heading[2] : line, anchorPrefix, openFile);
      container.append(block);
    }
    flushTable();
    return container;
  };
})();
