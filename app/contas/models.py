import hashlib
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from core.tenancy import TenantModel
from core.validators import limpar_documento, validar_cpf_ou_cnpj

from .papeis import Papel


class UsuarioManager(BaseUserManager):
    use_in_migrations = True

    def _criar(self, email, password, **extra):
        if not email:
            raise ValueError("E-mail é obrigatório.")
        usuario = self.model(email=self.normalize_email(email).lower(), **extra)
        usuario.set_password(password)
        usuario.save(using=self._db)
        return usuario

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._criar(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra["is_staff"] = True
        extra["is_superuser"] = True
        return self._criar(email, password, **extra)

    def get_by_natural_key(self, email):
        return self.get(email__iexact=email)


class Usuario(AbstractBaseUser, PermissionsMixin):
    """Usuário com login por e-mail. Não pertence a um escritório: o vínculo é a `Membership`."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField("e-mail", unique=True)
    nome = models.CharField(max_length=150)
    is_active = models.BooleanField("ativo", default=True)
    is_staff = models.BooleanField("equipe Tributo360", default=False)
    criado_em = models.DateTimeField(default=timezone.now)

    objects = UsuarioManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["nome"]

    class Meta:
        verbose_name = "usuário"
        constraints = [models.UniqueConstraint(models.functions.Lower("email"), name="usuario_email_unico_ci")]

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)


class Escritorio(models.Model):
    """O tenant: um escritório contábil cliente do Tributo360."""

    class Status(models.TextChoices):
        TRIAL = "trial", "Trial"
        ATIVO = "ativo", "Ativo"
        INADIMPLENTE = "inadimplente", "Inadimplente"
        CANCELADO = "cancelado", "Cancelado"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    razao_social = models.CharField(max_length=200)
    documento = models.CharField(
        "CPF/CNPJ do titular",
        max_length=14,
        validators=[validar_cpf_ou_cnpj],
        help_text="Sem máscara. CNPJ pode ser alfanumérico.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.TRIAL)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "escritório"

    def __str__(self):
        return self.razao_social

    def save(self, *args, **kwargs):
        self.documento = limpar_documento(self.documento)
        super().save(*args, **kwargs)


class Assinatura(models.Model):
    """Plano e limites do escritório. Cobrança e gateway entram na Fase 6."""

    class Status(models.TextChoices):
        TRIAL = "trial", "Trial"
        ATIVA = "ativa", "Ativa"
        INADIMPLENTE = "inadimplente", "Inadimplente"
        CANCELADA = "cancelada", "Cancelada"

    escritorio = models.OneToOneField(Escritorio, on_delete=models.CASCADE, related_name="assinatura")
    plano = models.CharField(max_length=30)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.TRIAL)
    limite_empresas = models.PositiveIntegerField()
    limite_usuarios = models.PositiveIntegerField()
    inicio = models.DateTimeField(default=timezone.now)
    fim = models.DateTimeField(null=True, blank=True)
    gateway_id = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return f"{self.escritorio} · {self.plano}"

    @classmethod
    def criar_para(cls, escritorio, plano=None):
        plano = plano or settings.PLANO_INICIAL
        cfg = settings.PLANOS[plano]
        fim = timezone.now() + timedelta(days=cfg["dias"]) if cfg["dias"] else None
        return cls.objects.create(
            escritorio=escritorio,
            plano=plano,
            status=cls.Status.TRIAL if plano == "trial" else cls.Status.ATIVA,
            limite_empresas=cfg["limite_empresas"],
            limite_usuarios=cfg["limite_usuarios"],
            fim=fim,
        )


class Membership(TenantModel):
    """Vínculo usuário × escritório, com papel e acesso às empresas.

    Com `acesso_todas_empresas` falso, a membership só enxerga as empresas em `empresas_permitidas`
    (relação definida em `empresas.Empresa.membros_restritos`).
    """

    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name="memberships")
    papel = models.CharField(max_length=20, choices=Papel.choices)
    acesso_todas_empresas = models.BooleanField(default=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["escritorio", "usuario"], name="membership_unica_por_escritorio"),
        ]

    def __str__(self):
        return f"{self.usuario} @ {self.escritorio_id} ({self.papel})"


def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Convite(TenantModel):
    """Convite por e-mail para entrar num escritório. Só o hash do token fica no banco."""

    email = models.EmailField()
    papel = models.CharField(max_length=20, choices=Papel.choices)
    acesso_todas_empresas = models.BooleanField(default=True)
    empresas = models.ManyToManyField("empresas.Empresa", blank=True, related_name="+")
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    expira_em = models.DateTimeField()
    criado_por = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, related_name="+")
    aceito_em = models.DateTimeField(null=True, blank=True)
    aceito_por = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    cancelado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-criado_em"]
        constraints = [
            models.UniqueConstraint(
                "escritorio",
                models.functions.Lower("email"),
                condition=models.Q(aceito_em__isnull=True, cancelado_em__isnull=True),
                name="convite_pendente_unico",
            ),
        ]

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)

    def gerar_token(self):
        """Gera um token novo, guarda o hash e devolve o token em claro (só existe no e-mail)."""
        token = secrets.token_urlsafe(32)
        self.token_hash = _hash_token(token)
        self.expira_em = timezone.now() + timedelta(days=settings.CONVITE_VALIDADE_DIAS)
        return token

    @classmethod
    def pendente_por_token(cls, token):
        """Busca global por token: o convite é a credencial, ainda não há membership."""
        return (
            cls.objects.select_related("escritorio")
            .filter(
                token_hash=_hash_token(token or ""),
                aceito_em__isnull=True,
                cancelado_em__isnull=True,
                expira_em__gt=timezone.now(),
            )
            .first()
        )

    @property
    def pendente(self):
        return self.aceito_em is None and self.cancelado_em is None and self.expira_em > timezone.now()
