from django.urls import path

from .views import aliquotas, catalogo, conta, documentos, empresas, simulacoes

app_name = "web"

urlpatterns = [
    path("", conta.inicio, name="inicio"),
    path("entrar/", conta.LoginView.as_view(), name="login"),
    path("sair/", conta.sair, name="sair"),
    path("criar-conta/", conta.cadastro, name="cadastro"),
    path("escritorios/", conta.escritorios, name="escritorios"),
    path("escritorios/novo/", conta.escritorio_novo, name="escritorio-novo"),
    path("motores/", conta.motores, name="motores"),
    # Empresas
    path("empresas/", empresas.lista, name="empresas"),
    path("empresas/nova/", empresas.nova, name="empresa-nova"),
    path("empresas/<uuid:pk>/", empresas.detalhe, name="empresa"),
    path("empresas/<uuid:pk>/editar/", empresas.editar, name="empresa-editar"),
    # Catálogo
    path("empresas/<uuid:pk>/itens/", catalogo.lista, name="catalogo"),
    path("empresas/<uuid:pk>/itens/novo/", catalogo.novo, name="item-novo"),
    path("empresas/<uuid:pk>/itens/importar/", catalogo.importar, name="catalogo-importar"),
    path("empresas/<uuid:pk>/itens/aprovar/", catalogo.aprovar, name="catalogo-aprovar"),
    path("itens/modelo.csv", catalogo.modelo, name="catalogo-modelo"),
    path("itens/<uuid:pk>/", catalogo.editar, name="item-editar"),
    path("itens/<uuid:pk>/revalidar/", catalogo.revalidar, name="item-revalidar"),
    path("itens/<uuid:pk>/inativar/", catalogo.inativar, name="item-inativar"),
    # Alíquotas efetivas do Simples
    path("empresas/<uuid:pk>/aliquotas-simples/", aliquotas.lista, name="aliquotas"),
    path("empresas/<uuid:pk>/aliquotas-simples/nova/", aliquotas.nova, name="aliquotas-nova"),
    path("aliquotas-simples/<uuid:pk>/", aliquotas.editar, name="aliquotas-editar"),
    path("aliquotas-simples/<uuid:pk>/remover/", aliquotas.remover, name="aliquotas-remover"),
    # Simulações
    path("empresas/<uuid:pk>/simular/", simulacoes.simular, name="simular"),
    path("simulacoes/", simulacoes.historico, name="historico"),
    path("simulacoes/comparar/", simulacoes.comparar, name="comparar"),
    path("simulacoes/<uuid:pk>/", simulacoes.detalhe, name="simulacao"),
    # Documentos fiscais (Fase 4)
    path("documentos/", documentos.todos, name="documentos"),
    path("documentos/importar/", documentos.importar_escolhendo, name="documentos-importar-geral"),
    path("documentos/tolerancia/", documentos.tolerancia, name="tolerancia"),
    path("empresas/<uuid:pk>/documentos/", documentos.da_empresa, name="documentos-empresa"),
    path("empresas/<uuid:pk>/documentos/importar/", documentos.importar, name="documentos-importar"),
    path("lotes/<uuid:pk>/", documentos.lote, name="lote"),
    path("documentos/<uuid:pk>/", documentos.documento, name="documento"),
    path("documentos/<uuid:pk>/conferir/", documentos.reconferir, name="documento-conferir"),
    path("documentos/<uuid:pk>/xml/", documentos.documento_xml, name="documento-xml"),
    path("documentos/<uuid:pk>/danfse/", documentos.documento_danfse, name="documento-danfse"),
    path("danfse/", documentos.danfse_avulso, name="danfse"),
    path("divergencias/<uuid:pk>/", documentos.divergencia, name="divergencia"),
]
