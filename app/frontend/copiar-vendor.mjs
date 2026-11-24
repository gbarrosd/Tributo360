// Copia HTMX, Alpine e as fontes IBM Plex de node_modules para app/static/vendor, com as licenças.
import { copyFileSync, mkdirSync } from "node:fs";

const destino = "../static/vendor";
const arquivos = [
  ["htmx.org/dist/htmx.min.js", "htmx/htmx.min.js"],
  ["htmx.org/LICENSE", "htmx/LICENSE"],
  ["alpinejs/dist/cdn.min.js", "alpine/alpine.min.js"],
  ["@fontsource/ibm-plex-sans/LICENSE", "fonts/LICENSE-IBM-Plex.txt"],
];
for (const peso of [400, 500, 600, 700]) {
  arquivos.push([`@fontsource/ibm-plex-sans/files/ibm-plex-sans-latin-${peso}-normal.woff2`, `fonts/ibm-plex-sans-${peso}.woff2`]);
}
for (const peso of [400, 500]) {
  arquivos.push([`@fontsource/ibm-plex-mono/files/ibm-plex-mono-latin-${peso}-normal.woff2`, `fonts/ibm-plex-mono-${peso}.woff2`]);
}
for (const [origem, alvo] of arquivos) {
  const caminho = `${destino}/${alvo}`;
  mkdirSync(caminho.slice(0, caminho.lastIndexOf("/")), { recursive: true });
  copyFileSync(`node_modules/${origem}`, caminho);
}
console.log(`${arquivos.length} arquivos copiados para static/vendor`);
