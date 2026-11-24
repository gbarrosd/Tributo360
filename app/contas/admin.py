from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Assinatura, Convite, Escritorio, Membership, Usuario


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    ordering = ["email"]
    list_display = ["email", "nome", "is_active", "is_staff", "criado_em"]
    search_fields = ["email", "nome"]
    fieldsets = (
        (None, {"fields": ("email", "nome", "password")}),
        ("Permissões", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Datas", {"fields": ("last_login", "criado_em")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "nome", "password1", "password2")}),)


class AssinaturaInline(admin.StackedInline):
    model = Assinatura


@admin.register(Escritorio)
class EscritorioAdmin(admin.ModelAdmin):
    list_display = ["razao_social", "documento", "status", "criado_em"]
    search_fields = ["razao_social", "documento"]
    inlines = [AssinaturaInline]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ["usuario", "escritorio", "papel", "acesso_todas_empresas", "ativo"]
    list_filter = ["papel", "ativo"]


@admin.register(Convite)
class ConviteAdmin(admin.ModelAdmin):
    list_display = ["email", "escritorio", "papel", "expira_em", "aceito_em", "cancelado_em"]
    exclude = ["token_hash"]
