# Componentes de terceiros

O Tributo360 executa, sem modificação, os motores de cálculo publicados pela Receita Federal do Brasil.
O código está em `engines/` como submódulos git presos nas tags oficiais.

| Componente | Versão | Licença | Titular |
|---|---|---|---|
| [calculadora-rtc-regime-regular](https://github.com/ReceitaFederal/calculadora-rtc-regime-regular) | 1.7.2 | MIT | Receita Federal do Brasil |
| [calculadora-rtc-simples-nacional](https://github.com/ReceitaFederal/calculadora-rtc-simples-nacional) | 1.0.2 | MIT | Receita Federal do Brasil |
| [calculadora-rtc-gerador-danfse](https://github.com/ReceitaFederal/calculadora-rtc-gerador-danfse) | 1.0.0 | MIT | Receita Federal do Brasil |

O texto integral de cada licença está no arquivo `LICENSE` do respectivo submódulo. As dependências
desses motores (Java: majoritariamente Apache 2.0; Go: Apache 2.0 e MIT, incluindo a biblioteca de PDF
`github.com/carlos7ags/folio`, Apache 2.0) estão levantadas em `docs/descoberta.md`, seção 6.

Dados de teste: `app/engines/tests/respostas/` guarda entradas e respostas gravadas dos motores (exemplos
oficiais do `docs/exemplos.md` e do OpenAPI de cada um) e `nfse_autorizada_teste.xml`, copiado dos testes
do gerador DANFSe. `app/documentos/tests/exemplos/` guarda XMLs de NF-e e NFS-e copiados de
`src/test/resources/xml` do motor do regime regular. Todos MIT, Copyright 2026 Receita Federal do Brasil.

Front-end, copiados sem modificação para `app/static/vendor/` por `app/frontend/copiar-vendor.mjs`:
[htmx](https://htmx.org) 2.0.4 (0BSD, Big Sky Software, com `LICENSE`), [Alpine.js](https://alpinejs.dev)
3.14.8 (MIT, Caleb Porzio and contributors) e as fontes [IBM Plex Sans e IBM Plex Mono](https://github.com/IBM/plex)
via Fontsource 5.2.6 (SIL Open Font License 1.1, IBM Corp., com `LICENSE-IBM-Plex.txt`). O CSS
`app/static/web/app.css` é gerado pelo [Tailwind CSS](https://tailwindcss.com) 4.1.11 (MIT, Tailwind Labs). Leitura de XLSX:
[openpyxl](https://openpyxl.readthedocs.io) (MIT).

Pendente antes do lançamento: validação jurídica e página "Componentes de código aberto" no produto.
