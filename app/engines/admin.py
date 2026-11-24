from django.contrib import admin

from .models import AliquotaNominal, ItemTabela, SincronizacaoTabela, VersaoMotor


class SomenteLeitura(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(VersaoMotor)
class VersaoMotorAdmin(SomenteLeitura):
    list_display = ["motor", "versao_app", "versao_db", "data_db", "vista_primeiro_em", "vista_por_ultimo_em"]
    list_filter = ["motor"]


@admin.register(AliquotaNominal)
class AliquotaNominalAdmin(admin.ModelAdmin):
    """Cadastro só por staff. Registros não são editados nem apagados: corrige-se com um novo."""

    list_display = ["esfera", "codigo_ibge", "aliquota", "vigencia_inicio", "vigencia_fim", "data_publicacao"]
    list_filter = ["esfera"]
    search_fields = ["codigo_ibge", "fonte"]
    exclude = ["cadastrada_por"]

    def has_change_permission(self, request, obj=None):
        return obj is None and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.cadastrada_por = request.user
        super().save_model(request, obj, form, change)


@admin.register(SincronizacaoTabela)
class SincronizacaoTabelaAdmin(SomenteLeitura):
    list_display = ["versao_motor", "status", "iniciada_em", "concluida_em"]


@admin.register(ItemTabela)
class ItemTabelaAdmin(SomenteLeitura):
    list_display = ["tabela", "codigo", "descricao", "versao_db_origem", "versao_db_ultima", "vigente"]
    list_filter = ["tabela", "vigente"]
    search_fields = ["codigo", "descricao"]
