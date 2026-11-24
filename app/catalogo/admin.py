from django.contrib import admin

from .models import ItemCatalogo


@admin.register(ItemCatalogo)
class ItemCatalogoAdmin(admin.ModelAdmin):
    list_display = ["codigo", "descricao", "empresa", "tipo", "ncm", "nbs", "c_class_trib", "status_classificacao"]
    list_filter = ["status_classificacao", "tipo"]
    search_fields = ["codigo", "descricao", "ncm", "nbs"]
    readonly_fields = ["validacao", "validado_em", "aprovado_por", "aprovado_em"]
