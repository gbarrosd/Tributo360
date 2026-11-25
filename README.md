# Tributo360

Plataforma SaaS para escritórios contábeis voltada à Reforma Tributária do Consumo (CBS, IBS e IS).

Status: **Fase 5 (relatórios em PDF e planilha)**. Descoberta e planejamento em `docs/descoberta.md` e `docs/planejamento.md`.

## Estrutura

```
app/                 Django 5.2 + DRF (o produto)
  config/            settings, urls, celery
  core/              isolamento multi-tenant (TenantModel, middleware), permissões, validadores, /healthz
  contas/            usuário (login por e-mail), escritório, assinatura, membership, papéis, convites
  empresas/          carteira de empresas do escritório
  auditoria/         registro de criação/edição/remoção com antes e depois
  engines/           clientes dos motores, contratos internos, versões, alíquotas nominais, tabelas oficiais
  simulacoes/        cálculos (regime regular e Simples) gravados com payload, resposta e versão;
                     alíquotas efetivas do Simples por empresa e mês (D3)
  catalogo/          produtos e serviços da empresa (NCM/NBS, CST, cClassTrib, cAtivSN), validação e aprovação
  web/               telas (Django Templates + Tailwind CSS + HTMX + Alpine.js)
  frontend/          fonte do tema (tailwind.css); gera static/web/app.css, o deploy não precisa de Node
engines/             motores da Receita como submódulos git, presos nas tags oficiais
docker/              Dockerfile dos motores Java e configuração do nginx
docker-compose.yml   mesmo compose para dev, homologação e a VPS
docker-compose.contrato.yml  só para testes de contrato: publica os motores em 127.0.0.1
```

## Subir tudo com Docker

```bash
git submodule update --init
cp .env.example .env          # troque DJANGO_SECRET_KEY e POSTGRES_PASSWORD
docker compose up -d --build  # a primeira build dos motores Java leva alguns minutos
curl http://localhost/healthz # versão de cada motor
docker compose exec django python manage.py createsuperuser
```

## Testar pelo navegador

1. Abra `http://localhost/` e entre com o e-mail e a senha do `createsuperuser`.
2. Um usuário sem escritório cai em **Escritórios** e cria o primeiro ali (vira dono). Pela linha de
   comando: `docker compose exec django python manage.py criar_escritorio --email voce@exemplo.com`.
   O escritório escolhido fica na sessão, então a API (`/api/motores/versoes/` etc.) também passa a
   responder pelo navegador, sem o cabeçalho `X-Escritorio`.
3. **Empresas → Nova empresa** (regime regular, CRT 3; ou Simples, CRT 1).
4. Na empresa, **Importar planilha** (baixe o modelo CSV na própria tela) ou **Novo item**. Itens com
   classificação errada vão para **Revisar** com o erro do motor; os demais esperam aprovação na
   **Fila de pendentes**.
5. Empresa do Simples: **Gerenciar períodos** e cadastre as alíquotas do PGDAS-D do mês.
6. **Nova simulação**: escolha itens do catálogo ou digite os códigos. Data a partir de 2027 preenche
   CBS/IBS com a tabela do Tributo360 (cadastrada no admin, em *Alíquotas nominais*), e você pode editar.
7. No resultado: memória de cálculo por item, origem de cada alíquota e versão do motor. **Duplicar como
   novo cenário** muda uma premissa; em **Simulações**, marque 2 a 4 e **Compare**. **Baixar PDF** e
   **Baixar planilha** geram o relatório da simulação.

## Telas

| Rota | O que faz |
|---|---|
| `/entrar/`, `/criar-conta/`, `/escritorios/` | Login, cadastro de escritório, escolha do escritório ativo (sessão) |
| `/empresas/` | Carteira: lista, cadastro, edição e página da empresa |
| `/empresas/{id}/itens/` | Catálogo, fila de pendentes, importação CSV/XLSX e aprovação |
| `/empresas/{id}/aliquotas-simples/` | Alíquotas efetivas do Simples por mês |
| `/empresas/{id}/simular/` | Formulário de simulação (regular ou Simples, conforme a empresa) |
| `/simulacoes/`, `/simulacoes/{id}/`, `/simulacoes/comparar/` | Histórico, resultado e comparação de cenários |
| `/simulacoes/{id}/relatorio.pdf`, `.xlsx` | Relatório da simulação: totais, premissas, alíquotas com origem e memória de cálculo; versões no rodapé |
| `/motores/` | Painel de versões dos motores |

As telas validam pelos mesmos serializers e serviços da API, e as views de `web/views/` passam pelo
teste de arquitetura (só `Model.tenant(request)`). `web/tests/test_isolamento.py` tenta abrir e alterar
objetos de outro escritório em toda tela com id na URL.

Serviços: nginx, django (gunicorn), celery worker e beat, postgres 16, redis e os três motores
(`motor-regime-regular` 1.7.2, `motor-simples` 1.0.2, `motor-danfse` 1.0.0). Os motores não têm
autenticação, então ficam só na rede interna do compose. E-mails (convite, redefinição de senha) saem no
log do container `django` enquanto `DJANGO_EMAIL_BACKEND` for o de console.

## Desenvolvimento sem Docker

```bash
cd app
pip install -r requirements-dev.txt
export DJANGO_DEBUG=1 POSTGRES_USER=... POSTGRES_PASSWORD=... POSTGRES_DB=...
python manage.py migrate
pytest            # inclui a suíte cross-tenant e o teste de arquitetura
ruff check . && ruff format --check .
```

### Visual (Tailwind + HTMX + Alpine)

O tema fica em `app/frontend/tailwind.css` (cores do tema claro e escuro como variáveis CSS, componentes
`btn`, `card`, `badge`, `alert`, `campo`, `tabela`...). O CSS gerado (`app/static/web/app.css`) e as cópias de
HTMX, Alpine e das fontes IBM Plex em `app/static/vendor/` ficam versionados, então o deploy e o Docker não
precisam de Node. Só quem mexer em template ou no tema precisa regerar:

```bash
cd app/frontend
npm ci
npm run dev      # observa os templates e regera o CSS enquanto você edita
npm run build    # CSS minificado + cópia de HTMX/Alpine/fontes; o CI confere que está em dia
```

Interatividade: filtros das listas por HTMX (`hx-select="#resultado"`, sem view parcial); menus, seleção em
tabelas e confirmações com Alpine no próprio template; `static/web/app.js` só cuida do tema e do formulário
de simulação. Tudo continua funcionando sem JavaScript.

## Isolamento entre escritórios

- Toda tabela de negócio herda de `core.tenancy.TenantModel` (campo `escritorio` obrigatório, id UUID).
- Views e serializers usam `Modelo.tenant(request)`, que filtra pelo escritório da membership ativa e,
  se ela tiver acesso restrito, pelas empresas permitidas. `core/tests/test_arquitetura.py` falha se
  encontrar `Modelo.objects` de um modelo de tenant em views ou serializers.
- O escritório ativo vem do cabeçalho `X-Escritorio`, da sessão (`POST /api/auth/escritorio/`) ou é o
  único do usuário. Escritório alheio responde 403; objeto de outro escritório responde 404.
- `core/tests/test_cross_tenant.py` tenta ler, editar e apagar dados do escritório B a partir do A em
  todas as rotas, e falha se um endpoint novo não for incluído na matriz.

## API

| Rota | O que faz |
|---|---|
| `GET /healthz` | Banco e versão de cada motor (`ok`, `degradado` ou 503 sem banco) |
| `GET /api/auth/csrf/` | Entrega o cookie CSRF |
| `POST /api/auth/cadastro/` | Cria usuário, escritório (trial) e o usuário como dono |
| `POST /api/auth/login/`, `/logout/`, `GET /api/auth/eu/` | Sessão |
| `POST /api/auth/escritorio/` | Troca o escritório ativo |
| `POST /api/auth/senha/esqueci/`, `/redefinir/`, `/alterar/` | Senha |
| `GET/PATCH /api/escritorio/` | Dados do escritório e limites do plano |
| `/api/membros/` | Lista, muda papel e acesso a empresas, remove |
| `/api/convites/` | Convida por e-mail, lista e cancela pendentes |
| `GET /api/convites/consultar/`, `POST /api/convites/aceitar/` | Tela e aceite do convite |
| `/api/empresas/` | CRUD de empresas (DELETE inativa) |
| `POST /api/simulacoes/regime-regular/` | Calcula uma operação do regime regular e grava a simulação |
| `POST /api/simulacoes/simples/` | Calcula no Simples com as alíquotas efetivas informadas (D3) |
| `GET /api/simulacoes/`, `/api/simulacoes/{id}/` | Histórico (filtros `empresa`, `tipo`) e detalhe com itens, payload, resposta e versão |
| `/api/itens/` | Catálogo (filtros `empresa`, `status`, `q`); `POST importar/` (CSV/XLSX), `POST aprovar/`, `POST {id}/revalidar/`, `GET modelo/` |
| `/api/aliquotas-simples/` | Alíquotas efetivas do Simples por empresa e mês (`periodo` = `AAAA-MM`) |
| `GET /api/motores/versoes/` | Painel de versões: o que roda agora, histórico e última sincronização de tabelas |
| `GET /api/aliquotas-nominais/vigentes/?data=&municipio=` | Alíquotas que a tabela do Tributo360 propõe (D2) |

Papéis e permissões seguem a matriz da seção 2.3 do planejamento (`app/contas/papeis.py`).

## Motores (Fase 2)

O resto do código nunca vê o JSON dos motores. `app/engines/contratos.py` define os objetos internos
(`OperacaoRegular`, `OperacaoSimples`, `ResultadoCalculo`...) e cada cliente em `app/engines/clientes/`
traduz para o formato do motor e de volta.

- **Endpoints usados**: `observabilidade/regime-geral` (erro por item, nunca o `/regime-geral`) e
  `observabilidade/simples-nacional/nao-assistido` (nunca o obsoleto), `xml/validate`, dados abertos,
  `/api/danfse` e os de versão.
- **Rede**: timeout de 5 s (2 s para conectar), uma nova tentativa só em erro de rede/5xx e circuit
  breaker por motor no cache (5 falhas em 60 s abrem por 30 s). Ajustável por `MOTORES_*` no ambiente.
  Motor fora do ar responde 503 na API; operação recusada inteira responde 422 com os erros do motor.
- **Versão**: toda `Simulacao` aponta para uma `VersaoMotor`, lida dos headers da própria resposta
  (`X-CALC-APP-VERSION`, `X-CALC-DB-VERSION`) e completada com a data da base por `GET /versao`.
  O worker do Celery registra as versões ao subir e o beat repete a cada 5 min
  (`python manage.py registrar_versoes_motores` faz o mesmo à mão).
- **Alíquotas nominais (D2)**: tabela global `AliquotaNominal` por esfera (União, UF, município) e
  vigência, cadastrada no admin. Não aceita registro sem fonte (também no banco), não se edita nem
  se apaga: corrige-se com registro novo, e vale o último. De 2027 em diante a simulação usa a tabela
  como padrão e o contador pode editar; cada item guarda a origem de cada alíquota
  (`tabela_tributo360`, `editada_pelo_usuario` com o valor anterior, ou `motor_2026`). Sem alíquota na
  tabela nem na requisição, a API responde 400 antes de chamar o motor.
- **Simples (D3)**: a simulação usa o cadastro `AliquotaEfetivaSN` do mês da operação. Sem cadastro, a
  API responde 400 (e a tela orienta o cadastro) sem chamar o motor. `aliquotas_efetivas` na
  requisição continua aceito, só para aquela simulação.
- **Tabelas oficiais**: CST, cClassTrib, NCM e NBS copiadas para `ItemTabela` pelo beat (de hora em
  hora; só baixa quando a `versao_db` muda). Nunca apaga: conteúdo novo vira registro novo e o que
  saiu da tabela oficial só perde `vigente`. À mão: `python manage.py sincronizar_tabelas [--forcar]`.

### Testes de contrato

Os testes comuns usam respostas reais gravadas dos motores (`app/engines/tests/respostas/`). Os de
contrato chamam os motores de verdade, com casos dourados (inclusive os exemplos oficiais), e rodam no
job `contrato` do CI. Na sua máquina:

```bash
docker compose -f docker-compose.yml -f docker-compose.contrato.yml up -d --build --wait \
  motor-regime-regular motor-simples motor-danfse
cd app
MOTORES_CONTRATO=1 MOTOR_REGIME_REGULAR_URL=http://localhost:18080 MOTOR_SIMPLES_URL=http://localhost:18082 \
  MOTOR_DANFSE_URL=http://localhost:18090 MOTOR_DANFSE_VERSAO=1.0.0 pytest -m contrato
```

Ao trocar a tag de um motor, um caso dourado que quebra mostra exatamente o que mudou no cálculo.
Atualize `VERSAO_RR`/`VERSAO_SN` e os valores esperados só depois de revisar a diferença.
