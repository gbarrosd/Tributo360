# Tributo360

Plataforma SaaS para escritórios contábeis voltada à Reforma Tributária do Consumo (CBS, IBS e IS).

Status: **Fase 1 (fundação)**. Descoberta e planejamento em `docs/descoberta.md` e `docs/planejamento.md`.

## Estrutura

```
app/                 Django 5.2 + DRF (o produto)
  config/            settings, urls, celery
  core/              isolamento multi-tenant (TenantModel, middleware), permissões, validadores, /healthz
  contas/            usuário (login por e-mail), escritório, assinatura, membership, papéis, convites
  empresas/          carteira de empresas do escritório
  auditoria/         registro de criação/edição/remoção com antes e depois
  engines/           app Django dos motores (por enquanto só a leitura de versão; clientes na Fase 2)
engines/             motores da Receita como submódulos git, presos nas tags oficiais
docker/              Dockerfile dos motores Java e configuração do nginx
docker-compose.yml   mesmo compose para dev, homologação e a VPS
```

## Subir tudo com Docker

```bash
git submodule update --init
cp .env.example .env          # troque DJANGO_SECRET_KEY e POSTGRES_PASSWORD
docker compose up -d --build  # a primeira build dos motores Java leva alguns minutos
curl http://localhost/healthz # versão de cada motor
docker compose exec django python manage.py createsuperuser
```

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

## Isolamento entre escritórios

- Toda tabela de negócio herda de `core.tenancy.TenantModel` (campo `escritorio` obrigatório, id UUID).
- Views e serializers usam `Modelo.tenant(request)`, que filtra pelo escritório da membership ativa e,
  se ela tiver acesso restrito, pelas empresas permitidas. `core/tests/test_arquitetura.py` falha se
  encontrar `Modelo.objects` de um modelo de tenant em views ou serializers.
- O escritório ativo vem do cabeçalho `X-Escritorio`, da sessão (`POST /api/auth/escritorio/`) ou é o
  único do usuário. Escritório alheio responde 403; objeto de outro escritório responde 404.
- `core/tests/test_cross_tenant.py` tenta ler, editar e apagar dados do escritório B a partir do A em
  todas as rotas, e falha se um endpoint novo não for incluído na matriz.

## API (Fase 1)

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

Papéis e permissões seguem a matriz da seção 2.3 do planejamento (`app/contas/papeis.py`).
