"""
core_logic.py

Core, UI-independent logic extracted and adapted from streamlit_app.py.
Provides:
- CoreState: encapsulates state previously in st.session_state
- GraphQL helpers
- Data fetching: investments, odds, jockey/trainer rankings, horse age
- Data aggregation and processing
- Plot generation functions that return Matplotlib/Plotly figures

Note: This is intended to be imported by the GUI (ui_components.py / main.py).
Replace or extend functions with your original detailed algorithms as needed.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import threading
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from dateutil import relativedelta as datere
from bs4 import BeautifulSoup
try:
    import plotly.graph_objects as go
except Exception:
    go = None
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import os

FONT_URL = "https://github.com/googlefonts/noto-cjk/raw/main/Sans/OTF/TraditionalChinese/NotoSansCJKtc-Regular.otf"
FONT_FILE = "NotoSansCJKtc-Regular.otf"


@dataclass
class CoreState:
    """A thread-safe replacement for Streamlit's session_state."""
    lock: threading.RLock = field(default_factory=threading.RLock)
    monitoring: bool = False
    reset: bool = False
    odds_dict: Dict[str, pd.DataFrame] = field(default_factory=lambda: {m: pd.DataFrame() for m in ['WIN','PLA','QIN','QPL','FCT','TRI','FF']})
    investment_dict: Dict[str, pd.DataFrame] = field(default_factory=lambda: {m: pd.DataFrame() for m in ['WIN','PLA','QIN','QPL','FCT','TRI','FF']})
    overall_investment_dict: Dict[str, pd.DataFrame] = field(default_factory=lambda: {m: pd.DataFrame() for m in ['WIN','PLA','QIN','QPL','FCT','TRI','FF','overall']})
    weird_dict: Dict[str, pd.DataFrame] = field(default_factory=lambda: {m: pd.DataFrame() for m in ['WIN','PLA','QIN','QPL','FCT','TRI','FF','overall']})
    diff_dict: Dict[str, pd.DataFrame] = field(default_factory=lambda: {m: pd.DataFrame() for m in ['WIN','PLA','QIN','QPL','FCT','TRI','FF','overall']})
    race_dict: Dict[str, Any] = field(default_factory=dict)
    post_time_dict: Dict[int, datetime] = field(default_factory=dict)
    numbered_list_dict: Dict[str, Any] = field(default_factory=dict)
    race_dataframes: Dict[int, pd.DataFrame] = field(default_factory=dict)
    ucb_dict: Dict[str, Any] = field(default_factory=dict)
    count_history: Dict[str, Any] = field(default_factory=dict)
    api_called: bool = False
    last_update: Optional[datetime] = None
    jockey_ranking_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    trainer_ranking_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    top_rank_history: List[Any] = field(default_factory=list)
    top_4_history: List[Any] = field(default_factory=list)
    horse_history: Dict[str, Any] = field(default_factory=dict)
    high_moneyflow_alerts: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["分鐘","時間", "馬號", "當刻賠率", "moneyflow"]))
    latest_results: Any = None


core_state = CoreState()


# ---------------- Utility: Chinese font loading (for Matplotlib) ----------------

def get_chinese_font() -> Optional[str]:
    """Ensure Chinese font exists and register it for Matplotlib. Returns filename or None."""
    try:
        if not os.path.exists(FONT_FILE):
            r = requests.get(FONT_URL, timeout=15)
            r.raise_for_status()
            with open(FONT_FILE, 'wb') as f:
                f.write(r.content)
        fm.fontManager.addfont(FONT_FILE)
        plt.rcParams['font.family'] = fm.FontProperties(fname=FONT_FILE).get_name()
        return FONT_FILE
    except Exception:
        return None


# ---------------- GraphQL / HTTP Helpers ----------------

GRAPHQL_URL = 'https://info.cld.hkjc.com/graphql/base/'


def _fetch_graphql_data(operation_name: str, query: str, variables: dict, url: str = GRAPHQL_URL, timeout: int = 10) -> Optional[dict]:
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0',
        'Referer': 'https://bet.hkjc.com/',
        'Origin': 'https://bet.hkjc.com',
        'Accept': '*/*'
    }
    payload = {"operationName": operation_name, "variables": variables, "query": query}
    session = requests.Session()
    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = session.post(url, headers=headers, json=payload, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            time.sleep(0.5)
        except Exception:
            time.sleep(0.5)
    return None


# ---------------- Data fetchers (adapted) ----------------

def get_investment_data(Date: str, place: str, race_no: int, methodlist: List[str]) -> Dict[str, List[float]]:
    """Fetch investment (pool) data via GraphQL and return as dict. Also stores raw result in core_state.
    """
    variables = {"date": str(Date), "venueCode": place, "raceNo": int(race_no), "oddsTypes": methodlist}
    query = """query racing($date: String, $venueCode: String, $oddsTypes: [OddsType], $raceNo: Int) { raceMeetings(date: $date, venueCode: $venueCode) { totalInvestment poolInvs: pmPools(oddsTypes: $oddsTypes, raceNo: $raceNo) { id leg { number races } status sellStatus oddsType investment mergedPoolId lastUpdateTime } } }"""
    data = _fetch_graphql_data("racing", query, variables)
    investments = {m: [] for m in ['WIN','PLA','QIN','QPL','FCT','TRI','FF']}
    if data and 'data' in data:
        race_meetings = data['data'].get('raceMeetings', [])
        for meeting in race_meetings:
            for pool in meeting.get('poolInvs', []):
                if place not in ['ST','HV']:
                    pool_id = pool.get('id')
                    if pool_id and pool_id[8:10] != place:
                        continue
                inv_val = pool.get('investment')
                if inv_val is not None:
                    try:
                        investments[pool.get('oddsType')].append(float(inv_val))
                    except Exception:
                        pass
    with core_state.lock:
        core_state.latest_results = core_state.latest_results or {}
        core_state.latest_results['investments'] = investments
    return investments


def get_odds_data(Date: str, place: str, race_no: int, methodlist: List[str]) -> Dict[str, Any]:
    """Fetch odds via GraphQL and store raw data in core_state."""
    variables = {"date": str(Date), "venueCode": place, "raceNo": int(race_no), "oddsTypes": methodlist}
    query = """query racing($date: String, $venueCode: String, $oddsTypes: [OddsType], $raceNo: Int) { raceMeetings(date: $date, venueCode: $venueCode) { pmPools(oddsTypes: $oddsTypes, raceNo: $raceNo) { id status sellStatus oddsType lastUpdateTime guarantee minTicketCost name_en name_ch leg { number races } cWinSelections { composite name_ch name_en starters } oddsNodes { combString oddsValue hotFavourite oddsDropValue bankerOdds { combString oddsValue } } } } }"""
    data = _fetch_graphql_data("racing", query, variables)
    odds_values = {m: [] for m in ['WIN','PLA','QIN','QPL','FCT','TRI','FF']}
    if data and 'data' in data:
        for meeting in data['data'].get('raceMeetings', []):
            for pool in meeting.get('pmPools', []):
                if place not in ['ST','HV']:
                    pool_id = pool.get('id')
                    if pool_id and pool_id[8:10] != place:
                        continue
                odds_nodes = pool.get('oddsNodes', [])
                odds_type = pool.get('oddsType')
                if not odds_type or odds_type not in odds_values:
                    continue
                odds_values[odds_type] = []
                for node in odds_nodes:
                    oddsValue = node.get('oddsValue')
                    if oddsValue == 'SCR':
                        val = np.inf
                    else:
                        try:
                            val = float(oddsValue)
                        except Exception:
                            continue
                    if odds_type in ["QIN","QPL","FCT","TRI","FF"]:
                        comb_string = node.get('combString')
                        if comb_string:
                            odds_values[odds_type].append((comb_string, val))
                    else:
                        odds_values[odds_type].append(val)
        for o_type in ["QIN","QPL","FCT","TRI","FF"]:
            if odds_values[o_type]:
                odds_values[o_type].sort(key=lambda x: x[0])
    with core_state.lock:
        core_state.latest_results = core_state.latest_results or {}
        core_state.latest_results['odds'] = odds_values
    return odds_values


def fetch_hkjc_jockey_ranking(season: str = "25/26") -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    query = """query rw_GetJockeyRanking($season: String) { jockeyStat(season: $season) { code name_ch name_en status id isCurSsn season ssnStat { numFirst numSecond numThird numFourth numFifth numStarts stakeWon trk ven } dhStat { numFirst numSecond numThird numFourth numFifth numStarts stakeWon trk ven } } }"""
    payload = {"operationName": "rw_GetJockeyRanking", "variables": {"season": season}, "query": query}
    headers = {"accept": "*/*", "content-type": "application/json", "User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.post(GRAPHQL_URL, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        result = resp.json()
        if isinstance(result, list):
            return None, f"API returned error list"
        data = result.get('data')
        if not data:
            return None, 'No data returned'
        jockeys = data.get('jockeyStat', [])
        if not jockeys:
            return None, f"No jockey data for season {season}"
        rows = []
        for j in jockeys:
            ssn_stats = j.get('ssnStat', [])
            stat_all = {}
            if isinstance(ssn_stats, list):
                for s in ssn_stats:
                    if s.get('trk') == 'ALL' and s.get('ven') == 'ALL':
                        stat_all = s
                        break
                if not stat_all and len(ssn_stats) > 0:
                    stat_all = ssn_stats[0]
            rows.append({
                "騎師編號": j.get('code'),
                "騎師": j.get('name_ch'),
                "英文名": j.get('name_en'),
                "勝": stat_all.get('numFirst', 0),
                "亞": stat_all.get('numSecond', 0),
                "季": stat_all.get('numThird', 0),
                "殿": stat_all.get('numFourth', 0),
                "第五": stat_all.get('numFifth', 0),
                "出賽": stat_all.get('numStarts', 0),
                "獎金": stat_all.get('stakeWon', 0),
                "賽季": j.get('season')
            })
        df = pd.DataFrame(rows)
        numeric_cols = ["勝","亞","季","殿","第五","出賽","獎金"]
        df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors='coerce').fillna(0)
        df["勝率 (%)"] = (df["勝"] / df["出賽"].replace(0,1) * 100).round(1)
        df = df.sort_values(by=["勝","亞","季"], ascending=False).reset_index(drop=True)
        df.insert(0, "排名", df.index + 1)
        return df, None
    except Exception as e:
        return None, str(e)


def fetch_hkjc_trainer_ranking(season: str = "25/26") -> Tuple[Optional[pd.DataFrame], Optional[str]]:
    query = """query rw_GetTrainerRanking($season: String) { trainerStat(season: $season) { code name_ch name_en status id isCurSsn season visitingIndex ssnStat { numFirst numSecond numThird numFourth numFifth numStarts stakeWon trk ven } dhStat { numFirst numSecond numThird numFourth numFifth numStarts stakeWon trk ven } } }"""
    payload = {"operationName": "rw_GetTrainerRanking", "variables": {"season": season}, "query": query}
    headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.post(GRAPHQL_URL, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        result = resp.json()
        if 'errors' in result:
            return None, result['errors'][0].get('message')
        data_section = result.get('data')
        if not data_section:
            return None, 'No data'
        trainers = data_section.get('trainerStat', [])
        if not trainers:
            return None, f"No trainer data for season {season}"
        rows = []
        for t in trainers:
            ssn_list = t.get('ssnStat', [])
            target_stat = {}
            if isinstance(ssn_list, list):
                for s in ssn_list:
                    if s.get('trk') == 'ALL' and s.get('ven') == 'ALL':
                        target_stat = s
                        break
                if not target_stat and len(ssn_list) > 0:
                    target_stat = ssn_list[0]
            rows.append({
                "練馬師": t.get('name_ch','').strip(),
                "勝": target_stat.get('numFirst',0),
                "亞": target_stat.get('numSecond',0),
                "季": target_stat.get('numThird',0),
                "出賽": target_stat.get('numStarts',0),
                "獎金": target_stat.get('stakeWon',0)
            })
        df = pd.DataFrame(rows)
        numeric_cols = ["勝","亞","季","出賽","獎金"]
        df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors='coerce').fillna(0)
        return df, None
    except Exception as e:
        return None, str(e)


def fetch_horse_age_only(date_val: str, place_val: str, race_no: int) -> Optional[pd.DataFrame]:
    if place_val in ['ST','HV']:
        base_url = "https://racing.hkjc.com/racing/information/Chinese/racing/RaceCard.aspx?"
        date_str = str(date_val).replace('-', '/')
        url = f"{base_url}RaceDate={date_str}&Racecourse={place_val}&RaceNo={race_no}"
        try:
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            table_rows = soup.find_all('tr', class_='f_tac f_fs13')
            age_data = []
            for row in table_rows:
                tds = row.find_all('td')
                if len(tds) > 16:
                    age_data.append({"編號": tds[0].text.strip(), "馬名": tds[3].text.strip(), "馬齡": tds[16].text.strip()})
            if age_data:
                return pd.DataFrame(age_data).set_index('編號')
            return None
        except Exception:
            return None
    return None


# ---------------- New: high-level scraping wrapper ----------------

def scrape_realtime(date: str, place: str, race_no: int, methodlist: List[str], status_callback: Optional[Any] = None) -> Dict[str, Any]:
    """Perform the real-time scraping steps and store results in core_state.
    status_callback (callable) receives status strings for UI updates.
    Returns a dict of results.

    This function now also updates core_state.odds_dict and investment_dict using helper save_* functions.
    """
    def _status(msg):
        if status_callback:
            try:
                status_callback(msg)
            except Exception:
                pass

    _status('Starting scraping')
    # normalize inputs
    Date = str(date)
    place_val = str(place)
    race_no_int = int(race_no)

    # 1) Fetch odds
    _status('Fetching odds data...')
    odds = get_odds_data(Date, place_val, race_no_int, methodlist)
    _status('Odds fetched')
    time.sleep(0.05)

    # 2) Fetch investment
    _status('Fetching investment data...')
    investments = get_investment_data(Date, place_val, race_no_int, methodlist)
    _status('Investments fetched')
    time.sleep(0.05)

    # 3) Persist into core_state time-series structures
    now_ts = datetime.utcnow()
    try:
        save_odds_data(now_ts, odds, methodlist)
        _status('Saved odds into state')
    except Exception as e:
        _status(f'Failed saving odds: {e}')
    try:
        save_investment_data(now_ts, investments, odds, methodlist)
        _status('Saved investments into state')
    except Exception as e:
        _status(f'Failed saving investments: {e}')

    # 4) perform weird/diff calculations
    try:
        calc = weird_data_calc(investments, odds, methodlist)
        _status('Performed diff calculations')
    except Exception as e:
        _status(f'Weird calc failed: {e}')

    # 5) Fetch horse age (HTML scrape)
    _status('Fetching horse age (page scrape)...')
    age_df = fetch_horse_age_only(Date, place_val, race_no_int)
    if age_df is not None:
        _status('Horse age fetched')
    else:
        _status('Horse age not available')

    # 6) Build display DataFrame combining WIN odds and investments (if present)
    display_df = None
    try:
        win_odds = odds.get('WIN', [])
        win_invest = investments.get('WIN', [])
        if win_odds:
            cols = [str(i+1) for i in range(len(win_odds))]
            odds_row = [float(x) if x != np.inf else np.nan for x in win_odds]
            invest_row = [float(x) if x is not None else np.nan for x in win_invest] if win_invest else [np.nan]*len(odds_row)
            display_df = pd.DataFrame([odds_row, invest_row], index=['odds','investment'], columns=cols)
    except Exception:
        display_df = None

    result = {
        'odds': odds,
        'investments': investments,
        'age': age_df,
        'display_df': display_df,
        'diff': calc if 'calc' in locals() else None
    }
    with core_state.lock:
        core_state.latest_results = result
    _status('Scraping complete')
    return result


# ---------------- Data saving / aggregation helpers ----------------
from typing import Callable

def investment_combined(time_now: datetime, df: pd.DataFrame) -> pd.DataFrame:
    sums = {}
    for col in df.columns:
        try:
            num1, num2 = col.split(',')
            num1, num2 = int(num1), int(num2)
        except Exception:
            continue
        col_sum = df[col].sum()
        sums[num1] = sums.get(num1, 0) + col_sum
        sums[num2] = sums.get(num2, 0) + col_sum
    sums_df = pd.DataFrame([sums], index=[time_now]) / 2
    return sums_df


def save_odds_data(time_now: datetime, odds: dict, methodlist: List[str]):
    """Populate core_state.odds_dict with the latest odds snapshot."""
    with core_state.lock:
        for method in methodlist:
            if method in ['WIN','PLA']:
                vals = odds.get(method, [])
                if not vals:
                    continue
                cols = list(range(1, len(vals)+1))
                df = core_state.odds_dict.get(method, pd.DataFrame())
                if df.empty:
                    df = pd.DataFrame(columns=cols)
                df.loc[time_now] = vals
                core_state.odds_dict[method] = df
            else:
                # combination types
                vals = odds.get(method, [])
                if not vals:
                    continue
                combs, odds_vals = zip(*vals)
                df = core_state.odds_dict.get(method, pd.DataFrame())
                if df.empty:
                    df = pd.DataFrame(columns=combs)
                df.loc[time_now] = odds_vals
                core_state.odds_dict[method] = df


def save_investment_data(time_now: datetime, investments: dict, odds: dict, methodlist: List[str]):
    """Populate core_state.investment_dict using investments and existing odds snapshot."""
    with core_state.lock:
        for method in methodlist:
            if method in ['WIN','PLA']:
                vals = odds.get(method, [])
                inv = investments.get(method, [])
                if not vals or not inv:
                    continue
                cols = list(range(1, len(vals)+1))
                df = core_state.investment_dict.get(method, pd.DataFrame())
                if df.empty:
                    df = pd.DataFrame(columns=cols)
                # compute investment per horse as pool / 1000 / odd if available
                try:
                    pool_total = inv[0]
                    invest_row = [round(pool_total / 1000 / float(o) if o not in (0, np.inf, None) else 0, 2) for o in vals]
                except Exception:
                    invest_row = [np.nan for _ in vals]
                df.loc[time_now] = invest_row
                core_state.investment_dict[method] = df
            else:
                vals = odds.get(method, [])
                inv = investments.get(method, [])
                if not vals or not inv:
                    continue
                combs, odds_vals = zip(*vals)
                df = core_state.investment_dict.get(method, pd.DataFrame())
                if df.empty:
                    df = pd.DataFrame(columns=combs)
                try:
                    pool_total = inv[0]
                    invest_row = [round(pool_total / 1000 / float(o) if o not in (0, np.inf, None) else 0, 2) for o in odds_vals]
                except Exception:
                    invest_row = [np.nan for _ in odds_vals]
                df.loc[time_now] = invest_row
                core_state.investment_dict[method] = df


def weird_data_calc(investments: dict, odds: dict, methodlist: List[str]) -> dict:
    """Compute diffs and simple anomalies; returns a dict of diffs stored in core_state.diff_dict."""
    diffs = {}
    with core_state.lock:
        for method in methodlist:
            try:
                inv_df = core_state.investment_dict.get(method, pd.DataFrame())
                odds_df = core_state.odds_dict.get(method, pd.DataFrame())
                if inv_df.empty or odds_df.empty or len(inv_df) < 1 or len(odds_df) < 2:
                    continue
                latest_invest = inv_df.tail(1).values
                last_time_odds = odds_df.tail(2).head(1).values
                if last_time_odds.size == 0:
                    continue
                pool_total = investments.get(method, [0])[0]
                expected = pool_total / 1000 / last_time_odds
                expected = np.where(last_time_odds == np.inf, 0, expected)
                diff = np.round(latest_invest - expected, 0)
                diff_df = pd.DataFrame(diff, columns=inv_df.columns, index=[list(inv_df.index)[-1]])
                core_state.diff_dict[method] = pd.concat([core_state.diff_dict.get(method, pd.DataFrame()), diff_df]) if not core_state.diff_dict.get(method, pd.DataFrame()).empty else diff_df
                diffs[method] = diff_df
            except Exception:
                continue
    return diffs

# ---------------- Plot helpers ----------------

def make_bar_figure(df: pd.DataFrame, odds_df: Optional[pd.DataFrame] = None, title: str = "") -> plt.Figure:
    fig, ax1 = plt.subplots(figsize=(12,6))
    df = df.fillna(0)
    X = df.columns
    X_axis = np.arange(len(X))
    ax1.bar(X_axis, df.iloc[-1], 0.6, color='pink')
    ax1.set_xticks(X_axis)
    ax1.set_xticklabels([str(x) for x in X], fontsize=10)
    ax1.set_ylabel('投注額')
    fig.suptitle(title)
    fig.tight_layout()
    return fig


def make_bubble_figure(total_volume_df: pd.DataFrame, diff_win: pd.Series, diff_qin: pd.Series, race_no: int, method_name: List[str]):
    df = pd.DataFrame({
        'horse': total_volume_df.columns.astype(str),
        'ΔI': diff_win.values,
        'ΔQ': diff_qin.values,
        '總投注量': total_volume_df.iloc[0].fillna(0).round(0).astype(int).values
    })
    df = df[df['總投注量'] > 0]
    if df.empty:
        return go.Figure()
    raw_size = df['總投注量']
    bubble_size = 20 + (raw_size - raw_size.min()) / (raw_size.max() - raw_size.min() + 1e-6) * 80
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df['ΔI'], y=df['ΔQ'], mode='markers+text', text=df['horse'], textposition='middle center', marker=dict(size=bubble_size, color=df['ΔI'], colorscale='Bluered_r', reversescale=True, line=dict(width=1, color='white'), opacity=0.8), customdata=df['總投注量']))
    fig.add_hline(y=0, line_color='lightgrey')
    fig.add_vline(x=0, line_color='lightgrey')
    fig.update_layout(title=f"{method_name} 氣泡圖 (第{race_no}場)", xaxis_title=method_name[0], yaxis_title=method_name[1], height=500)
    return fig


# ---------------- Convenience: top() simplified output ----------------
def top_simple(method_odds_df: pd.DataFrame, method_investment_df: pd.DataFrame, method: str, time_delay: int = 5) -> Dict[str, pd.DataFrame]:
    """Simplified port of original `top` — returns plain DataFrames for UI display."""
    result = {"main_table": None, "plus_table": None, "plus_df": None}
    try:
        if method_odds_df.empty or method_investment_df.empty:
            return result
        first_row_odds = method_odds_df.iloc[0]
        last_row_odds = method_odds_df.iloc[-1]
        first_row_odds_df = first_row_odds.to_frame(name='Odds').reset_index()
        first_row_odds_df.columns = ['Combination','Odds']
        last_row_odds_df = last_row_odds.to_frame(name='Odds').reset_index()
        last_row_odds_df.columns = ['Combination','Odds']
        # build simple merged table
        final_df = last_row_odds_df.merge(method_investment_df.iloc[-1].to_frame(name='Investment').reset_index().rename(columns={0:'Investment','index':'Combination'}), on='Combination', how='left')
        result['main_table'] = final_df
        result['plus_df'] = final_df
    except Exception:
        pass
    return result


if __name__ == '__main__':
    get_chinese_font()
    print('Core logic module loaded')
