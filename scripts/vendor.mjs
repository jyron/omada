import { mkdir, copyFile } from 'node:fs/promises';
const dest = 'desk/static/vendor';
await mkdir(dest, { recursive: true });
for (const [source, file] of [
  ['bootstrap/dist/css/bootstrap.min.css', 'bootstrap.min.css'],
  ['bootstrap/dist/js/bootstrap.bundle.min.js', 'bootstrap.bundle.min.js'],
  ['bootstrap/LICENSE', 'bootstrap-LICENSE.txt'],
  ['bootstrap-icons/bootstrap-icons.svg', 'bootstrap-icons.svg'],
  ['bootstrap-icons/LICENSE', 'bootstrap-icons-LICENSE.txt'],
]) await copyFile(`node_modules/${source}`, `${dest}/${file}`);
for (const weight of [400, 500, 600]) {
  const file = `ibm-plex-sans-latin-${weight}-normal.woff2`;
  await copyFile(`node_modules/@fontsource/ibm-plex-sans/files/${file}`, `${dest}/${file}`);
}
await copyFile('node_modules/@fontsource/ibm-plex-sans/LICENSE', `${dest}/ibm-plex-sans-LICENSE.txt`);
