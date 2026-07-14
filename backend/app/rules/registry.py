from app.rules.base import Rule
from app.rules.implementations import (
    CardNotPresentRule,
    DifferentCountryRule,
    HighAmountRule,
    HighRiskMerchantRule,
    ImpossibleTravelRule,
    MultipleDeclinesRule,
    NewDeviceRule,
    NewMerchantRule,
    NightTransactionRule,
    VelocityRule,
)

RULES: list[Rule] = [
    HighAmountRule(),
    ImpossibleTravelRule(),
    VelocityRule(),
    NewDeviceRule(),
    NewMerchantRule(),
    HighRiskMerchantRule(),
    DifferentCountryRule(),
    NightTransactionRule(),
    MultipleDeclinesRule(),
    CardNotPresentRule(),
]


def all_rules() -> list[Rule]:
    return RULES
