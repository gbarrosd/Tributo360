from django.contrib import admin

from .models import Empresa


@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ["razao_social", "cnpj", "escritorio", "regime", "uf", "ativa"]
    list_filter = ["regime", "uf", "ativa"]
    search_fields = ["razao_social", "cnpj"]
