#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf

try:
    from tabulate import tabulate
    HAVE_TABULATE = True
except Exception:
    HAVE_TABULATE = False


def classic_pivots(high: float, low: float, close: float) -> Dict[str, float]:
    P = (high + low + close) / 3.0
    R1 = 2 * P - low
    S1 = 2 * P - high
    R2 = P + (high - low)
    S2 = P - (high - low)
    R3 = high + 2 * (P - low)
    S3 = low - 2 * (high - P)
    return {"P": P, "R1": R1, "S1": S1, "R2": R2, "S2": S2, "R3": R3, "S3": S3}


def fib_pivots(high: float, low: float, close: float) -> Dict[str, float]:
    P = (high + low + close) / 3.0
    r = high - low
    return {
        "P": P,
        "R1": P + 0.382 * r, "S1": P - 0.382 * r,
        "R2": P + 0.618 * r, "S2": P - 0.618 * r,
        "R3": P + 1.000 * r, "S3": P - 1.000 * r,
    }


def last_full_session_ohlc(df_daily: pd.DataFrame) -> Optional[Tuple[float, float, float]]:
    """
    Возвращает (high, low, close) последней ПОЛНОЙ дневной свечи.
    Отбрасываем сегодняшнюю дату (по локальному времени).
    """
    if df_daily is None or df_daily.empty:
        return None
    df = df_daily[['High', 'Low', 'Close']].dropna()
    df = df[df.index.date < date.today()]
    if df.empty:
        return None
    last = df.iloc[-1]
    return float(last['High']), float(last['Low']), float(last['Close'])


def read_tickers_from_file(path: Path) -> List[str]:
    """
    Читает тикеры из файла. Допускает форматы:
      - по одному тикеру в строке
      - через запятую в одной строке
    Пропускает пустые строки и комментарии, начинающиеся с #.
    """
    tickers: List[str] = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = [p.strip() for p in line.replace(';', ',').split(',') if p.strip()]
        tickers.extend(parts)
    return sorted(set(t.upper() for t in tickers))


def compute_for_ticker(df_panel: pd.DataFrame, t: str, method: str) -> Optional[Dict[str, object]]:
    """
    Берёт срез по одному тикеру из панельного датафрейма yfinance.download()
    и считает уровни + средний объём за ~3 месяца.
    """
    # yfinance.download возвращает MultiIndex колонок: ('High','Low','Close','Volume',...) x тикер
    if isinstance(df_panel.columns, pd.MultiIndex):
        if t not in df_panel.columns.levels[1]:
            return None
        df = df_panel.xs(t, axis=1, level=1)
    else:
        # если скачивали один тикер — колонки обычные
        df = df_panel

    ohlc = last_full_session_ohlc(df)
    if ohlc is None:
        return None
    high, low, close = ohlc

    # усреднённый дневной объём за ~3 месяца (60 последних торговых дней)
    # если данных меньше, mean() посчитает по доступным.
    if 'Volume' in df.columns:
        avg_volume = float(pd.to_numeric(df['Volume'], errors='coerce').tail(60).mean())
    else:
        avg_volume = float('nan')

    levels = classic_pivots(high, low, close) if method == 'classic' else fib_pivots(high, low, close)
    return {
        "ticker": t,
        "prev_high": high,
        "prev_low": low,
        "prev_close": close,
        "avg_3m_volume": avg_volume,   # новая колонка
        **levels
    }


def main():
    p = argparse.ArgumentParser(
        description="Pivot/S&R для нескольких тикеров (classic или fib) на основе предыдущего дня."
    )
    p.add_argument("--tickers", nargs="*", default=[],
                   help="Список тикеров через пробел: AAPL MSFT TSLA …")
    p.add_argument("--tickers-file", type=Path,
                   help="Путь к файлу со списком тикеров (по одному в строке или через запятую).")
    p.add_argument("--method", choices=["classic", "fib"], default="classic",
                   help="Метод расчёта (по умолчанию classic).")
    p.add_argument("--lookback-days", type=int, default=20,
                   help="Сколько дней загрузить для надёжного отбора последней полной сессии.")
    p.add_argument("--out-csv", type=Path, help="Сохранить результат в CSV.")
    p.add_argument("--out-json", type=Path, help="Сохранить результат в JSON.")
    p.add_argument("--raw", action="store_true", help="Простой текстовый вывод без tabulate.")
    args = p.parse_args()

    tickers: List[str] = []
    tickers.extend([t.upper() for t in (args.tickers or [])])
    if args.tickers_file:
        tickers.extend(read_tickers_from_file(args.tickers_file))

    tickers = sorted(set([t for t in tickers if t]))
    if not tickers:
        print("Укажите тикеры через --tickers или --tickers-file")
        return

    # Пакетная загрузка — быстрее и экономнее запросов
    period = f"{max(args.lookback_days, 7)}d"
    try:
        df_panel = yf.download(
            tickers=tickers,
            period=period,
            interval="1d",
            auto_adjust=False,
            group_by="column",   # получим MultiIndex колонок
            threads=True,
            progress=False
        )
    except Exception as e:
        print(f"Ошибка загрузки котировок: {e}")
        return

    results: List[Dict[str, object]] = []
    for t in tickers:
        row = compute_for_ticker(df_panel, t, args.method)
        if row:
            results.append(row)
        else:
            results.append({"ticker": t, "error": "no-data-or-no-full-session"})

    df_out = pd.DataFrame(results)

    # Вывод на экран
    if args.raw or not HAVE_TABULATE:
        print(df_out.to_string(index=False))
    else:
        cols = ["ticker", "prev_high", "prev_low", "prev_close", "avg_3m_volume",
                "P", "R1", "S1", "R2", "S2", "R3", "S3"]
        cols = [c for c in cols if c in df_out.columns]
        # floatfmt для красивого вывода чисел; объём выведется как число без разделителей
        print(tabulate(df_out[cols], headers="keys", tablefmt="github", floatfmt=".4f", showindex=False))

    # Сохранение
    if args.out_csv:
        df_out.to_csv(args.out_csv, index=False)
        print(f"\nCSV сохранён: {args.out_csv}")
    if args.out_json:
        df_out.to_json(args.out_json, orient="records", force_ascii=False, indent=2)
        print(f"JSON сохранён: {args.out_json}")


if __name__ == "__main__":
    main()

