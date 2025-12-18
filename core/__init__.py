"""Ядро спільних (SSOT) утиліт проєкту.

Цей пакет містить лише загальні, доменно-нейтральні будівельні блоки:
- серіалізацію/десеріалізацію;
- форматтери для UI/логів;
- контракти (схеми payload) між шарами.

Бізнес-логіка та доменні правила мають жити у відповідних модулях Stage*/SMC/UI.
"""

from __future__ import annotations

from . import formatters as formatters
from . import serialization as serialization

__all__ = [
    "formatters",
    "serialization",
]
