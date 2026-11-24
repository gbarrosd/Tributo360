"""Cliente do gerador de DANFSe (`danfse-service`, em Go)."""

from django.conf import settings

from ..contratos import Motor, VersaoInfo
from ..http import ClienteMotor, OperacaoRecusada

# Situação da NFS-e impressa no DANFSe.
STC_VALIDA, STC_SUBSTITUIDA, STC_CANCELADA = 0, 1, 2


class DanfseRecusado(OperacaoRecusada):
    """O gerador não conseguiu ler o XML. A mensagem vem do serviço (às vezes técnica, ex.: "EOF")."""


class DanfseClient(ClienteMotor):
    motor = Motor.DANFSE

    def gerar(self, xml: bytes, stc=STC_VALIDA, canhoto=False, contexto=None) -> bytes:
        """Gera o PDF a partir do XML de uma NFS-e nacional autorizada."""
        if stc not in (STC_VALIDA, STC_SUBSTITUIDA, STC_CANCELADA):
            raise ValueError("stc deve ser 0 (válida), 1 (substituída) ou 2 (cancelada).")
        resposta, _ = self.requisitar(
            "POST",
            "/api/danfse",
            contexto=contexto,
            params={"stc": stc, "canhoto": "true" if canhoto else "false"},
            content=xml,
            headers={"Content-Type": "application/xml"},
        )
        if resposta.status_code == 200 and resposta.content.startswith(b"%PDF"):
            return resposta.content
        corpo = self.json_ou_vazio(resposta)
        mensagem = (corpo.get("mensagem") if isinstance(corpo, dict) else None) or "Não foi possível gerar o DANFSe."
        raise DanfseRecusado(
            self.motor, f"O XML não pôde ser convertido em DANFSe: {mensagem}", status=resposta.status_code
        )

    def versao(self) -> VersaoInfo:
        """O gerador não expõe versão por HTTP: confere o probe e usa a versão fixada no compose."""
        resposta, _ = self.requisitar("GET", "/health/ready")
        if resposta.status_code != 200:
            raise self.recusa(resposta)
        return VersaoInfo(motor=self.motor, versao_app=settings.MOTORES[self.motor].get("versao_fixa") or "")
