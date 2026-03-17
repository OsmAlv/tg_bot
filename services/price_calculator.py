from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


BRV_UZS = 412_000
DELIVERY_USD = 5_000


@dataclass
class PriceResult:
    price_korea_usd: float
    final_price_usd: float


class PriceCalculator:
    def _brv_to_usd(self, brv_amount: float, usd_uzs: float) -> float:
        return (brv_amount * BRV_UZS) / usd_uzs

    def _resolve_duty_multiplier(self, duty_multiplier: float | None = None) -> float:
        """
        Коэффициент к ввозной пошлине в зависимости от сертификата происхождения:
        - 1.0: нет сертификата происхождения (полные пошлины: 80%+6, 60%+6, 80%+6)
        - 0.5: есть сертификат происхождения (пошлины снижены вдвое: 40%+3, 30%+3, 40%+3)

        По умолчанию применяется 1.0 (без сертификата).
        """
        if duty_multiplier is None:
            return 1.0
        if duty_multiplier < 0:
            return 1.0
        return duty_multiplier

    def _get_per_cc_rate_under_1_year(self, engine_cc: int) -> float:
        """
        ПП-3818 ТН ВЭД 8703: легковые автомобили до 1 года по объёму двигателя.
        8703211xxx  ≤1000cc   → 15% + 0.4 $/cc
        8703221xxx  1000-1500 → 15% + 0.6 $/cc
        8703231941  1500-1800 → 15% + 0.8 $/cc
        8703231981+ 1800-3000 → 15% + 0.8 $/cc
        8703241011+ >3000     → 15% + 1.25 $/cc
        """
        if engine_cc <= 1000:
            return 0.4
        elif engine_cc <= 1500:
            return 0.6
        elif engine_cc <= 3000:  # 1500-3000cc: 0.8 $/cc (НЕ 2.5!)
            return 0.8
        else:  # > 3000cc
            return 1.25

    def _get_per_cc_rate_1_to_3_years(self, engine_cc: int) -> float:
        """
        ПП-3818 ТН ВЭД 8703: легковые автомобили 1-3 года по объёму двигателя.
        """
        if engine_cc <= 1000:
            return 1.8
        elif engine_cc <= 1200:
            return 2.0
        elif engine_cc <= 1500:
            return 2.0
        elif engine_cc <= 2500:
            return 2.5
        else:  # > 2500
            return 3.0

    def _get_service_brv(self, customs_value_usd: float) -> float:
        """
        Таможенный сбор за таможенное оформление (БРВ) — КМ №700 от 09.11.2020.
        Ставка зависит от таможенной стоимости товара (CIF = цена + доставка).
        Применяется ко всем возрастным категориям.
        """
        if customs_value_usd <= 10_000:
            return 1.0
        elif customs_value_usd <= 20_000:
            return 1.5
        elif customs_value_usd <= 40_000:
            return 2.5
        elif customs_value_usd <= 60_000:
            return 4.0
        elif customs_value_usd <= 100_000:
            return 8.0
        elif customs_value_usd <= 200_000:
            return 15.0
        elif customs_value_usd <= 500_000:
            return 30.0
        elif customs_value_usd <= 1_000_000:
            return 58.0
        else:
            return 75.0

    def _get_utilization_brv_over_3_years(self, engine_cc: int) -> float:
        """
        Утилизационный сбор (БРВ) для автомобилей более 3 лет по объёму
        в режиме стороннего калькулятора.
        """
        if engine_cc < 1000:
            return 90
        elif engine_cc < 1500:
            return 120
        elif engine_cc < 2000:
            return 210
        elif engine_cc < 3000:
            return 300
        else:
            return 480

    def _customs_under_1_year(
        self,
        car_price_usd: float,
        engine_cc: int,
        usd_uzs: float,
        duty_multiplier: float = 1.0,
    ) -> float:
        """
        Таможенный расчёт для автомобилей до 1 года.
        Формула (режим стороннего бота): 30% от CIF + 2/2.5 $/cc + НДС 12% +
        таможенный сбор (по CIF) + утилизация 120/300 БРВ (по объёму).
        Пошлина и НДС рассчитываются на CIF (цена + доставка).
        """
        per_cc_usd = 2.0 if engine_cc <= 3000 else 2.5
        utilization_brv = 120 if engine_cc <= 3000 else 300
        cif_value = car_price_usd + DELIVERY_USD  # для расчета таможенного сбора
        customs_service_brv = self._get_service_brv(cif_value)

        # Пошлина и НДС на базе CIF (цена + доставка)
        customs_duty = ((0.30 * cif_value) + (per_cc_usd * engine_cc)) * duty_multiplier
        vat = 0.12 * (cif_value + customs_duty)
        utilization_fee = self._brv_to_usd(utilization_brv, usd_uzs)
        customs_service_fee = self._brv_to_usd(customs_service_brv, usd_uzs)
        return customs_duty + vat + utilization_fee + customs_service_fee

    def _customs_1_to_3_years(
        self,
        car_price_usd: float,
        engine_cc: int,
        usd_uzs: float,
        duty_multiplier: float = 1.0,
    ) -> float:
        """
        Таможенный расчёт для автомобилей 1-3 года.
        Формула (режим стороннего бота): 60% от CIF + 6 $/cc + НДС 12% +
        таможенный сбор (по CIF) + утилизация 300 БРВ.
        Пошлина и НДС рассчитываются на CIF (цена + доставка).
        """
        per_cc_usd = 6.0
        cif_value = car_price_usd + DELIVERY_USD  # для расчета таможенного сбора
        customs_service_brv = self._get_service_brv(cif_value)

        # Пошлина и НДС на базе CIF (цена + доставка)
        customs_duty = ((0.60 * cif_value) + (per_cc_usd * engine_cc)) * duty_multiplier
        vat = 0.12 * (cif_value + customs_duty)
        utilization_fee = self._brv_to_usd(300, usd_uzs)
        customs_service_fee = self._brv_to_usd(customs_service_brv, usd_uzs)
        return customs_duty + vat + utilization_fee + customs_service_fee

    def _customs_over_3_years(
        self,
        car_price_usd: float,
        engine_cc: int,
        usd_uzs: float,
        duty_multiplier: float = 1.0,
    ) -> float:
        """
        Таможенный расчёт для автомобилей более 3 лет.
        Формула (режим стороннего бота): 80% от CIF + 6 $/cc + НДС 12% +
        таможенный сбор (по CIF) + утилизация по объёму.
        Пошлина и НДС рассчитываются на CIF (цена + доставка).
        """
        utilization_brv = self._get_utilization_brv_over_3_years(engine_cc)
        cif_value = car_price_usd + DELIVERY_USD  # для расчета таможенного сбора
        customs_service_brv = self._get_service_brv(cif_value)

        # Пошлина и НДС на базе CIF (цена + доставка)
        customs_duty = ((0.80 * cif_value) + (6.0 * engine_cc)) * duty_multiplier
        vat = 0.12 * (cif_value + customs_duty)
        utilization_fee = self._brv_to_usd(utilization_brv, usd_uzs)
        customs_service_fee = self._brv_to_usd(customs_service_brv, usd_uzs)
        return customs_duty + vat + utilization_fee + customs_service_fee

    def _detect_car_age(self, car_year: int) -> int:
        current_year = datetime.now().year
        age = current_year - car_year
        return max(age, 0)

    def calculate(
        self,
        car_price_usd: float,
        car_year: int,
        engine_cc: int,
        usd_uzs: float,
        duty_multiplier: float | None = None,
    ) -> PriceResult:
        age = self._detect_car_age(car_year)
        resolved_multiplier = self._resolve_duty_multiplier(duty_multiplier)

        if age < 1:
            customs = self._customs_under_1_year(
                car_price_usd,
                engine_cc,
                usd_uzs,
                duty_multiplier=resolved_multiplier,
            )
        elif age <= 3:
            customs = self._customs_1_to_3_years(
                car_price_usd,
                engine_cc,
                usd_uzs,
                duty_multiplier=resolved_multiplier,
            )
        else:
            customs = self._customs_over_3_years(
                car_price_usd,
                engine_cc,
                usd_uzs,
                duty_multiplier=resolved_multiplier,
            )

        subtotal = car_price_usd + DELIVERY_USD + customs
        final_price = subtotal * 1.05  # Наценка 5%

        return PriceResult(
            price_korea_usd=round(car_price_usd, 2),
            final_price_usd=round(final_price, 2),
        )