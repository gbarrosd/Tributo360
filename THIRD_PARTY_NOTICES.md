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

Pendente antes do lançamento: validação jurídica e página "Componentes de código aberto" no produto.
