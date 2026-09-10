const fs = require('node:fs/promises');
const path = require('node:path');
const { watch } = require('node:fs');
const postcss = require('postcss');
const root = path.resolve(__dirname, '..');
process.chdir(root);
const input = path.join(root, 'assets/css/style.css');
const output = path.join(root, 'assets/css/style.compiled.css');
let timer;
async function build() {
  const { default: config } = await import('../postcss.config.js');
  const result = await postcss(config.plugins).process(await fs.readFile(input, 'utf8'), { from: input, to: output, map: false });
  await fs.writeFile(output, result.css);
  console.log('CSS built: assets/css/style.compiled.css');
}
build().catch(error => { console.error(error); process.exitCode = 1; });
if (process.argv.includes('--watch')) {
  const onChange = (_, file) => {
    if (!file || /node_modules|style\.compiled\.css|package-lock/.test(file)) return;
    clearTimeout(timer);
    timer = setTimeout(() => build().catch(console.error), 150);
  };
  try { watch(root, { recursive: true }, onChange); }
  catch (error) {
    if (error.code !== 'ERR_FEATURE_UNAVAILABLE_ON_PLATFORM') throw error;
    const { readdirSync } = require('node:fs');
    function watchDirectory(directory) {
      watch(directory, (event, file) => onChange(event, file && path.relative(root, path.join(directory, file))));
      for (const entry of readdirSync(directory, { withFileTypes: true })) {
        if (entry.isDirectory() && !['node_modules', '.git', 'coverage'].includes(entry.name)) watchDirectory(path.join(directory, entry.name));
      }
    }
    watchDirectory(root);
  }
}
