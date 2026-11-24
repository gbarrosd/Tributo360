from django.contrib import admin

from .models import Simulacao, SimulacaoItem


class ItemInline(admin.TabularInline):
    model = SimulacaoItem
    extra = 0
    can_delete = False
    fields = ["numero", "estado", "ncm", "nbs", "cst", "c_class_trib", "base_calculo", "valores", "erros"]
    readonly_fields = fields


@admin.register(Simulacao)
class SimulacaoAdmin(admin.ModelAdmin):
    list_display = ["criado_em", "escritorio", "empresa", "tipo", "situacao", "versao_motor"]
    list_filter = ["tipo", "situacao"]
    inlines = [ItemInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
