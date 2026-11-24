"""Configuração do Tributo360.

Tudo que muda entre ambientes vem de variáveis de ambiente (ver `.env.example` na raiz).
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env(nome, padrao=None):
    return os.environ.get(nome, padrao)


def env_bool(nome, padrao=False):
    valor = os.environ.get(nome)
    if valor is None:
        return padrao
    return valor.strip().lower() in {"1", "true", "sim", "yes", "on"}


def env_lista(nome, padrao=""):
    return [item.strip() for item in env(nome, padrao).split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("Defina DJANGO_SECRET_KEY (ou DJANGO_DEBUG=1 em desenvolvimento).")
    SECRET_KEY = "dev-inseguro-nao-use-em-producao"

ALLOWED_HOSTS = env_lista("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_lista("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "core",
    "contas",
    "empresas",
    "auditoria",
    "engines",
    "simulacoes",
    "catalogo",
    "web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.tenancy.TenantMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "web.contexto.sessao",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", "tributo360"),
        "USER": env("POSTGRES_USER", "tributo360"),
        "PASSWORD": env("POSTGRES_PASSWORD", ""),
        "HOST": env("POSTGRES_HOST", "localhost"),
        "PORT": env("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": int(env("POSTGRES_CONN_MAX_AGE", "60")),
        "CONN_HEALTH_CHECKS": True,
    }
}

REDIS_URL = env("REDIS_URL")
if REDIS_URL:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_URL}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

AUTH_USER_MODEL = "contas.Usuario"

# Política mínima de senha (critério de aceite da Fase 1).
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
        "OPTIONS": {"user_attributes": ("email", "nome")},
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    {"NAME": "contas.validators.LetrasENumerosValidator"},
]
PASSWORD_RESET_TIMEOUT = 60 * 60 * 2  # 2 horas

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

LOGIN_URL = "web:login"
LOGIN_REDIRECT_URL = "web:inicio"
LOGOUT_REDIRECT_URL = "web:login"
# Limite de tentativas de login pela tela (mesma taxa da API).
WEB_LOGIN_TENTATIVAS = int(env("WEB_LOGIN_TENTATIVAS", "10"))
WEB_LOGIN_JANELA_SEGUNDOS = int(env("WEB_LOGIN_JANELA_SEGUNDOS", "60"))
# Upload de planilha do catálogo (o nginx aceita até 50 MB; a regra do catálogo corta em 5 MB).
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Sessão e segurança --------------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 12
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_COOKIES = env_bool("DJANGO_SECURE_COOKIES", not DEBUG)
SESSION_COOKIE_SECURE = SECURE_COOKIES
CSRF_COOKIE_SECURE = SECURE_COOKIES
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
if env_bool("DJANGO_BEHIND_TLS_PROXY", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# Só confia em X-Real-IP quando há um proxy nosso (nginx) na frente.
CONFIAR_X_REAL_IP = env_bool("DJANGO_TRUST_X_REAL_IP", False)

# --- E-mail ----------------------------------------------------------------------
EMAIL_BACKEND = env("DJANGO_EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "localhost")
EMAIL_PORT = int(env("EMAIL_PORT", "587"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "Tributo360 <nao-responda@tributo360.local>")
# Base dos links enviados por e-mail (convite, redefinição de senha).
FRONTEND_URL = env("FRONTEND_URL", "http://localhost:8000").rstrip("/")

# --- DRF -------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_THROTTLE_RATES": {
        "auth": env("THROTTLE_AUTH", "10/min"),
    },
    "EXCEPTION_HANDLER": "core.exceptions.handler",
}

# --- Celery ----------------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", REDIS_URL or "redis://localhost:6379/0")
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", False)
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    # Mantém VersaoMotor em dia mesmo sem cálculos (painel de versões).
    "registrar-versoes-motores": {"task": "engines.registrar_versoes", "schedule": 300.0},
    # Barato quando nada mudou: só consulta /versao e sai se a base já foi copiada.
    "sincronizar-tabelas-oficiais": {"task": "engines.sincronizar_tabelas", "schedule": 3600.0},
}

# --- Motores da Receita (rede interna do compose) ----------------------------------
MOTORES = {
    "regime_regular": {
        "url": env("MOTOR_REGIME_REGULAR_URL", "http://motor-regime-regular:8080"),
        "versao_path": "/api/calculadora/dados-abertos/versao",
    },
    "simples": {
        "url": env("MOTOR_SIMPLES_URL", "http://motor-simples:8082"),
        "versao_path": "/api/versao",
    },
    "danfse": {
        "url": env("MOTOR_DANFSE_URL", "http://motor-danfse:8080"),
        # O gerador DANFSe não expõe versão por HTTP: usamos o probe e a tag do submódulo.
        "versao_path": "/health/ready",
        "versao_fixa": env("MOTOR_DANFSE_VERSAO", ""),
    },
}
MOTORES_TIMEOUT = float(env("MOTORES_TIMEOUT", "2"))  # /healthz e painel de versões
# Cálculos e demais chamadas (planejamento, seção 3): 5 s no total, 2 s para conectar.
MOTORES_TIMEOUT_CALCULO = float(env("MOTORES_TIMEOUT_CALCULO", "5"))
MOTORES_TIMEOUT_CONEXAO = float(env("MOTORES_TIMEOUT_CONEXAO", "2"))
# Circuit breaker: N falhas dentro da janela abrem o circuito por alguns segundos.
MOTORES_CIRCUITO_FALHAS = int(env("MOTORES_CIRCUITO_FALHAS", "5"))
MOTORES_CIRCUITO_JANELA_SEGUNDOS = int(env("MOTORES_CIRCUITO_JANELA_SEGUNDOS", "60"))
MOTORES_CIRCUITO_ABERTO_SEGUNDOS = int(env("MOTORES_CIRCUITO_ABERTO_SEGUNDOS", "30"))
MOTORES_VERSAO_CACHE_SEGUNDOS = int(env("MOTORES_VERSAO_CACHE_SEGUNDOS", "300"))

# --- Planos (limites provisórios até a Fase 6 definir cobrança) ----------------------
PLANOS = {
    "trial": {"limite_empresas": 10, "limite_usuarios": 3, "dias": 14},
    "essencial": {"limite_empresas": 30, "limite_usuarios": 3, "dias": None},
    "profissional": {"limite_empresas": 100, "limite_usuarios": 10, "dias": None},
}
PLANO_INICIAL = "trial"

CONVITE_VALIDADE_DIAS = 7

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}
