import re

from django.core.exceptions import ValidationError


class LetrasENumerosValidator:
    """Exige ao menos uma letra e um número na senha."""

    def validate(self, password, user=None):
        if not (re.search(r"[A-Za-z]", password) and re.search(r"[0-9]", password)):
            raise ValidationError("A senha precisa ter letras e números.", code="senha_sem_letras_e_numeros")

    def get_help_text(self):
        return "A senha precisa ter letras e números."
