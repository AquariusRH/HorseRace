# core_logic.py
import requests
import time
import pandas as pd
import numpy as np
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from PySide6.QtCore import QThread, Signal

# -------------------------
# GraphQL & Scrape Helpers
# -------------------------
def _fetch_graphql_data(operation_name, query, variables, timeout=15, max_retries=3, log_fn=print):
    """
    Verbatim-structure GraphQL helper (copied structure from streamlit_app.py)
    Returns parsed JSON or None on failure.
    Logs request URL and status codes via log_fn.
    """
    url = 'https://info.cld.hkjc.com/graphql/base/'
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Referer': 'https://bet.hkjc.com/',
        'Origin': 'https://bet.hkjc.com',
        'Accept': '*/*',
        'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-site',
    }
    payload = {
        "operationName": operation_name,
        "variables": variables,
        "query": query
    }

    session = requests.Session()
    for attempt in range(1, max_retries + 1):
        try:
            log_fn(f"[GraphQL] POST {url} op={operation_name} attempt={attempt} variables={variables}")
            resp = session.post(url, json=payload, headers=headers, timeout=timeout)
            log_fn(f"[GraphQL] status_code={resp.status_code}")
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log_fn(f"[GraphQL] request failed attempt={attempt}: {e}")
            time.sleep(0.5)
    log_fn("[GraphQL] max_retries exceeded, returning None")
    return None

# Exact query strings copied from streamlit_app.py (kept as multiline strings)
INVESTMENT_QUERY = """
query racing($date: String, $venueCode: String, $oddsTypes: [OddsType], $raceNo: Int) {
  raceMeetings(date: $date, venueCode: $venueCode) {
    totalInvestment
    poolInvs: pmPools(oddsTypes: $oddsTypes, raceNo: $raceNo) {
      id
      leg {
        number
        races
      }
      status
      sellStatus
      oddsType
      investment
      mergedPoolId
      lastUpdateTime
    }
  }
}
"""

ODDS_QUERY = """
query racing($date: String, $venueCode: String, $oddsTypes: [OddsType], $raceNo: Int) {
  raceMeetings(date: $date, venueCode: $venueCode) {
    pmPools(oddsTypes: $oddsTypes, raceNo: $raceNo) {
      id
      status
      sellStatus
      oddsType
      lastUpdateTime
      guarantee
      minTicketCost
      name_en
      name_ch
      leg {
        number
        races
      }
      cWinSelections {
        composite
        name_ch
        name_en
        starters
      }
      oddsNodes {
        combString
        oddsValue
        hotFavourite
        oddsDropValue
        bankerOdds {
          combString
          oddsValue
        }
      }
    }
  }
}
"""

JOCKEY_RANKING_QUERY = """query rw_GetJockeyRanking($season: String) {
  jockeyStat(season: $season) {
    code
    name_ch
    name_en
    status
    id
    isCurSsn
    season
    ssnStat {
      numFirst
      numSecond
      numThird
      numFourth
      numFifth
      numStarts
      stakeWon
      trk
      ven
    }
    dhStat {
      numFirst
      numSecond
      numThird
      numFourth
      numFifth
      numStarts
      stakeWon
      trk
      ven
    }
  }
}"""

TRAINER_RANKING_QUERY = """query rw_GetTrainerRanking($season: String) {
  trainerStat(season: $season) {
    code
    name_ch
    name_en
    status 
    id
    isCurSsn
    season
    visitingIndex
    ssnStat {
      numFirst
      numSecond
      numThird
      numFourth
      numFifth
      numStarts
      stakeWon
      trk
      ven
    }
    dhStat {
      numFirst
      numSecond
      numThird
      numFourth
      numFifth
      numStarts
      stakeWon
      trk
      ven
    }
  }
}
"""

def fetch_hkjc_jockey_ranking(log_fn=print):
    season = "25/26"
    payload_vars = {"season": season}
    try:
        log_fn("[JockeyRanking] fetching")
        result = _fetch_graphql_data("rw_GetJockeyRanking", JOCKEY_RANKING_QUERY, payload_vars, log_fn=log_fn)
        if result is None:
            return None, "no response"
        if isinstance(result, list):
            return None, f"API 返回錯誤列表: {result[0].get('message')}"
        data = result.get("data")
        if not data:
            err = result.get("errors", [{}])[0].get("message", "Unknown error")
            return None, f"GraphQL 錯誤: {err}"
        jockeys = data.get("jockeyStat", [])
        if not jockeys:
            return None, f"找不到賽季 {season} 的資料"
        rows = []
        for j in jockeys:
            ssn_stats = j.get("ssnStat", [])
            stat_all = {}
            if isinstance(ssn_stats, list):
                for s in ssn_stats:
                    if s.get("trk") == "ALL" and s.get("ven") == "ALL":
                        stat_all = s
                        break
                if not stat_all and len(ssn_stats) > 0:
                    stat_all = ssn_stats[0]
            rows.append({
                "騎師編號": j.get("code"),
                "騎師": j.get("name_ch"),
                "英文名": j.get("name_en"),
                "勝": stat_all.get("numFirst", 0),
                "亞": stat_all.get("numSecond", 0),
                "季": stat_all.get("numThird", 0),
                "殿": stat_all.get("numFourth", 0),
                "第五": stat_all.get("numFifth", 0),
                "出賽": stat_all.get("numStarts", 0),
                "獎金": stat_all.get("stakeWon", 0),
                "賽季": j.get("season")
            })
        df = pd.DataFrame(rows)
        numeric_cols = ["勝", "亞", "季", "殿", "第五", "出賽", "獎金"]
        df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors='coerce').fillna(0)
        df["勝率 (%)"] = (df["勝"] / df["出賽"].replace(0, 1) * 100).round(1)
        df = df.sort_values(by=["勝", "亞", "季"], ascending=False).reset_index(drop=True)
        df.insert(0, "排名", df.index + 1)
        return df, None
    except Exception as e:
        return None, f"系統抓取異常: {e}"

def fetch_hkjc_trainer_ranking(log_fn=print):
    season = "25/26"
    payload_vars = {"season": season}
    try:
        log_fn("[TrainerRanking] fetching")
        result = _fetch_graphql_data("rw_GetTrainerRanking", TRAINER_RANKING_QUERY, payload_vars, log_fn=log_fn)
        if result is None:
            return None, "no response"
        if "errors" in result:
            return None, f"GraphQL 錯誤: {result['errors'][0].get('message')}"
        data_section = result.get("data")
        if not data_section:
            return None, "API 回傳 data 欄位為空"
        trainers = data_section.get("trainerStat", [])
        if not trainers:
            return None, f"找不到賽季 {season} 的練馬師資料"
        rows = []
        for t in trainers:
            ssn_list = t.get("ssnStat", [])
            target_stat = {}
            if isinstance(ssn_list, list):
                for s in ssn_list:
                    if s.get("trk") == "ALL" and s.get("ven") == "ALL":
                        target_stat = s
                        break
                if not target_stat and len(ssn_list) > 0:
                    target_stat = ssn_list[0]
            rows.append({
                "練馬師": t.get("name_ch", "").strip(),
                "勝": target_stat.get("numFirst", 0),
                "亞": target_stat.get("numSecond", 0),
                "季": target_stat.get("numThird", 0),
                "出賽": target_stat.get("numStarts", 0),
                "獎金": target_stat.get("stakeWon", 0)
            })
        df = pd.DataFrame(rows)
        numeric_cols = ["勝", "亞", "季", "出賽", "獎金"]
        df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors='coerce').fillna(0)
        return df, None
    except Exception as e:
        return None, f"抓取異常: {e}"

def fetch_horse_age_only(date_val, place_val, race_no, log_fn=print):
    """
    Returns DataFrame indexed by 編號 with 馬名 and 馬齡 columns.
    """
    if place_val in ['ST','HV']:
        base_url = "https://racing.hkjc.com/racing/information/Chinese/racing/RaceCard.aspx?"
        date_str = str(date_val).replace('-', '/')
        url = f"{base_url}RaceDate={date_str}&Racecourse={place_val}&RaceNo={race_no}"
        try:
            log_fn(f"[HorseAge] GET {url}")
            response = requests.get(url, timeout=20)
            log_fn(f"[HorseAge] status_code={response.status_code}")
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                table_rows = soup.find_all('tr', class_='f_tac f_fs13')
                age_data = []
                for row in table_rows:
                    tds = row.find_all('td')
                    if len(tds) > 16 and tds[16]:
                        age_data.append({
                            "編號": tds[0].text.strip(),
                            "馬名": tds[3].text.strip(),
                            "馬齡": tds[16].text.strip()
                        })
                if age_data:
                    return pd.DataFrame(age_data).set_index("編號")
            return None
        except Exception as e:
            log_fn(f"[HorseAge] exception: {e}")
            return None
    return None

# -------------------------
# Investment & Odds Saving (session-like dicts)
# -------------------------
def investment_combined(time_now, method, df):
    """
    Combine pair-combination investments into per-horse sums.
    """
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

# -------------------------
# QThread DataEngineWorker
# -------------------------
class DataEngineWorker(QThread):
    """
    Background data-only worker. Emits pure-data dicts via data_ready_signal.
    It does NOT touch any GUI objects or create figures/HTML.
    """
    data_ready_signal = Signal(dict)   # dict of DataFrames and arrays
    log_signal = Signal(str)
    stopped_signal = Signal()

    def __init__(self, race_date, place, race_no, methodlist, time_delay=5, parent=None):
        super().__init__(parent)
        self.race_date = race_date
        self.place = place
        self.race_no = int(race_no)
        self.methodlist = methodlist[:]  # list copy
        self.time_delay = float(time_delay)
        self.running = False

        # local state (mirror of st.session_state dicts)
        self.odds_dict = {m: pd.DataFrame() for m in methodlist}
        self.investment_dict = {m: pd.DataFrame() for m in methodlist}
        self.overall_investment_dict = {m: pd.DataFrame() for m in methodlist}
        self.diff_dict = {m: pd.DataFrame() for m in methodlist}
        self.overall_investment_dict['overall'] = pd.DataFrame()
        self.diff_dict['overall'] = pd.DataFrame()

    def log(self, msg):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        s = f"[Worker][{timestamp}] {msg}"
        print(s)
        try:
            self.log_signal.emit(s)
        except Exception:
            pass

    def reset_state(self):
        """Clear internal histories."""
        self.log("Resetting internal worker state.")
        self.odds_dict = {m: pd.DataFrame() for m in self.methodlist}
        self.investment_dict = {m: pd.DataFrame() for m in self.methodlist}
        self.overall_investment_dict = {m: pd.DataFrame() for m in self.methodlist}
        self.overall_investment_dict['overall'] = pd.DataFrame()
        self.diff_dict = {m: pd.DataFrame() for m in self.methodlist}
        self.diff_dict['overall'] = pd.DataFrame()

    def _save_odds(self, time_now, odds):
        for method in self.methodlist:
            if method in ['WIN', 'PLA']:
                if self.odds_dict.get(method) is None or self.odds_dict[method].empty:
                    self.odds_dict[method] = pd.DataFrame(columns=np.arange(1, len(odds.get(method, [])) + 1))
                # ensure we have proper length, fill if needed
                try:
                    self.odds_dict[method].loc[time_now] = odds.get(method, [])
                except Exception:
                    # fallback: convert to list of floats
                    vals = [float(v) if v != 'SCR' else np.inf for v in odds.get(method, [])]
                    self.odds_dict[method].loc[time_now] = vals
            elif method in ['QIN', 'QPL', 'FCT', 'TRI', 'FF']:
                if odds.get(method):
                    combination, odds_array = zip(*odds[method])
                    if self.odds_dict.get(method) is None or self.odds_dict[method].empty:
                        self.odds_dict[method] = pd.DataFrame(columns=combination)
                    self.odds_dict[method].loc[time_now] = odds_array

    def _save_investments(self, time_now, investments, odds):
        for method in self.methodlist:
            if method in ['WIN', 'PLA']:
                if self.investment_dict.get(method) is None or self.investment_dict[method].empty:
                    self.investment_dict[method] = pd.DataFrame(columns=np.arange(1, len(odds.get(method, [])) + 1))
                investment_df = []
                for odd in odds.get(method, []):
                    try:
                        investment_df.append(round(investments[method][0] / 1000 / odd, 2))
                    except Exception:
                        investment_df.append(0.0)
                self.investment_dict[method].loc[time_now] = investment_df
            elif method in ['QIN','QPL','FCT','TRI','FF']:
                if odds.get(method):
                    combination, odds_array = zip(*odds[method])
                    if self.investment_dict.get(method) is None or self.investment_dict[method].empty:
                        self.investment_dict[method] = pd.DataFrame(columns=combination)
                    investment_df = []
                    for odd in odds_array:
                        try:
                            investment_df.append(round(investments[method][0] / 1000 / odd, 2))
                        except Exception:
                            investment_df.append(0.0)
                    self.investment_dict[method].loc[time_now] = investment_df

    def _weird_diff_calc(self, time_now, investments, odds):
        """
        Ported weird_data logic that computes diff between latest investment and expected.
        """
        for method in self.methodlist:
            try:
                # need at least two entries
                if self.investment_dict.get(method) is None or self.investment_dict[method].empty or len(self.investment_dict[method]) < 1:
                    continue
                latest_investment = self.investment_dict[method].tail(1).values
                last_time_odds_df = self.odds_dict[method].tail(2).head(1) if not self.odds_dict.get(method, pd.DataFrame()).empty else pd.DataFrame()
                if last_time_odds_df.empty:
                    continue
                last_time_odds = last_time_odds_df.values
                pool_total = investments.get(method, [0])[0]
                expected = pool_total / 1000 / last_time_odds
                expected = np.where(last_time_odds == np.inf, 0, expected)
                diff = np.round(latest_investment - expected, 0)
                diff_df = pd.DataFrame(diff, columns=self.investment_dict[method].columns, index=[time_now])
                if method in ['WIN', 'PLA']:
                    self.diff_dict[method] = pd.concat([self.diff_dict.get(method, pd.DataFrame()), diff_df])
                elif method in ['QIN', 'QPL']:
                    combined_diff = investment_combined(time_now, method, diff_df)
                    self.diff_dict[method] = pd.concat([self.diff_dict.get(method, pd.DataFrame()), combined_diff])
            except Exception as e:
                self.log(f"[weird_diff_calc] error for {method}: {e}")

    def _compute_overall(self, time_now):
        """
        Compute overall investment totals per horse combining WIN/PLA and QIN/QPL aggregates.
        """
        try:
            # Determine number of horses from WIN columns if available
            no_of_horse = None
            if 'WIN' in self.investment_dict and not self.investment_dict['WIN'].empty:
                no_of_horse = len(self.investment_dict['WIN'].columns)
            else:
                # fallback to any non-empty investment
                for k, v in self.investment_dict.items():
                    if not v.empty:
                        no_of_horse = len(v.columns)
                        break
            if no_of_horse is None:
                return
            total_investment_df = pd.DataFrame(index=[time_now], columns=np.arange(1, no_of_horse + 1))
            for horse in range(1, no_of_horse + 1):
                total = 0.0
                for method in self.methodlist:
                    if method in ['WIN', 'PLA']:
                        try:
                            total += float(self.overall_investment_dict.get(method, pd.DataFrame()).iloc[-1][horse])
                        except Exception:
                            total += 0.0
                    elif method in ['QIN', 'QPL']:
                        try:
                            total += float(self.overall_investment_dict.get(method, pd.DataFrame()).iloc[-1].get(horse, 0))
                        except Exception:
                            total += 0.0
                total_investment_df[horse] = total
            self.overall_investment_dict['overall'] = pd.concat([self.overall_investment_dict.get('overall', pd.DataFrame()), total_investment_df])
        except Exception as e:
            self.log(f"[compute_overall] error: {e}")

    def compute_henery_model(self, latest_odds_df, latest_invest_df):
        """
        Compute Henery model predictions using latest odds and investments.
        Returns a DataFrame with predictions. Entirely pure-data operation.
        """
        try:
            # latest_odds_df: Series of odds per horse (WIN), latest_invest_df: Series of investment per horse
            if latest_odds_df is None or latest_invest_df is None:
                return pd.DataFrame()
            # convert to numeric
            odds = pd.to_numeric(latest_odds_df.astype(float), errors='coerce').replace({np.inf: np.nan}).fillna(9999)
            invest = pd.to_numeric(latest_invest_df.astype(float), errors='coerce').fillna(0.0)
            implied_prob = (1.0 / odds).replace([np.inf, np.nan], 0.0)
            implied_prob = implied_prob / implied_prob.sum() if implied_prob.sum() > 0 else implied_prob

            # theoretical prob via normalized investments (avoid zero-division)
            invest_sum = invest.sum()
            theoretical = (invest / invest_sum).fillna(0.0) if invest_sum > 0 else pd.Series(0, index=invest.index)

            # value index: implied/theoretical (higher is more overvalued implied; we invert to find value)
            # avoid divide-by-zero
            value_index = pd.Series(0, index=invest.index)
            for idx in invest.index:
                try:
                    if theoretical.loc[idx] > 0:
                        value_index.loc[idx] = implied_prob.loc[idx] / theoretical.loc[idx]
                    else:
                        value_index.loc[idx] = np.inf if implied_prob.loc[idx] > 0 else 0
                except Exception:
                    value_index.loc[idx] = 0

            expected_edge = (theoretical - implied_prob)  # positive means expected edge
            suggested_fraction = expected_edge.clip(lower=0) * 0.1  # conservative Kelly-like fraction scaled down

            df = pd.DataFrame({
                "odds": odds,
                "implied_prob": (implied_prob * 100).round(2),
                "theoretical_prob": (theoretical * 100).round(2),
                "value_index": value_index.round(3),
                "expected_edge_pct": (expected_edge * 100).round(2),
                "suggested_fraction": suggested_fraction.round(4)
            })
            df.index.name = "horse"
            return df.sort_values(by="value_index", ascending=False)
        except Exception as e:
            self.log(f"[henery_model] exception: {e}")
            return pd.DataFrame()

    def run_once_cycle(self):
        """
        One scrape/compute cycle. Prepares a data payload dict and emits it via data_ready_signal.
        """
        time_now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        # 1) Fetch investments (GraphQL)
        try:
            variables = {"date": str(self.race_date), "venueCode": self.place, "raceNo": int(self.race_no), "oddsTypes": self.methodlist}
            investments_json = _fetch_graphql_data("racing", INVESTMENT_QUERY, variables, log_fn=self.log)
            # parse investments
            investments = {"WIN": [], "PLA": [], "QIN": [], "QPL": [], "FCT": [], "TRI": [], "FF": []}
            if investments_json and 'data' in investments_json:
                meetings = investments_json['data'].get('raceMeetings', [])
                for meeting in meetings:
                    pool_invs = meeting.get('poolInvs', [])
                    for pool in pool_invs:
                        if self.place not in ['ST','HV']:
                            pool_id = pool.get('id')
                            if pool_id and pool_id[8:10] != self.place:
                                continue
                        inv_val = pool.get('investment')
                        try:
                            investments[pool.get('oddsType')].append(float(inv_val))
                        except Exception:
                            pass
            self.log(f"[cycle] investments fetched: { {k: len(v) for k, v in investments.items()} }")
        except Exception as e:
            self.log(f"[cycle] investments fetch exception: {e}")
            investments = {"WIN": [], "PLA": [], "QIN": [], "QPL": [], "FCT": [], "TRI": [], "FF": []}

        # 2) Fetch odds
        try:
            variables = {"date": str(self.race_date), "venueCode": self.place, "raceNo": int(self.race_no), "oddsTypes": self.methodlist}
            odds_json = _fetch_graphql_data("racing", ODDS_QUERY, variables, log_fn=self.log)
            odds_values = {"WIN": [], "PLA": [], "QIN": [], "QPL": [], "FCT": [], "TRI": [], "FF": []}
            if odds_json and 'data' in odds_json:
                race_meetings = odds_json['data'].get('raceMeetings', [])
                for meeting in race_meetings:
                    pm_pools = meeting.get('pmPools', [])
                    for pool in pm_pools:
                        if self.place not in ['ST','HV']:
                            pool_id = pool.get('id')
                            if pool_id and pool_id[8:10] != self.place:
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
                            if odds_type in ["QIN", "QPL", "FCT", "TRI", "FF"]:
                                comb_string = node.get('combString')
                                if comb_string:
                                    odds_values[odds_type].append((comb_string, val))
                            else:
                                odds_values[odds_type].append(val)
                for o_type in ["QIN", "QPL", "FCT", "TRI", "FF"]:
                    if odds_values[o_type]:
                        odds_values[o_type].sort(key=lambda x: x[0])
            self.log(f"[cycle] odds fetched: { {k: len(v) if not isinstance(v, list) else len(v) for k, v in odds_values.items()} }")
        except Exception as e:
            self.log(f"[cycle] odds fetch exception: {e}")
            odds_values = {"WIN": [], "PLA": [], "QIN": [], "QPL": [], "FCT": [], "TRI": [], "FF": []}

        # 3) Save into worker-local histories
        try:
            self._save_odds(time_now, odds_values)
            self._save_investments(time_now, investments, odds_values)
            self._weird_diff_calc(time_now, investments, odds_values)
            self._compute_overall(time_now)
        except Exception as e:
            self.log(f"[cycle] saving/processing exception: {e}")

        # 4) Build payload to emit: copies of latest DataFrames (safe copies)
        payload = {}
        for m in self.methodlist:
            payload[f"{m}_odds"] = self.odds_dict.get(m).copy() if isinstance(self.odds_dict.get(m), pd.DataFrame) else pd.DataFrame()
            payload[f"{m}_investment"] = self.investment_dict.get(m).copy() if isinstance(self.investment_dict.get(m), pd.DataFrame) else pd.DataFrame()
            payload[f"{m}_diff"] = self.diff_dict.get(m).copy() if isinstance(self.diff_dict.get(m), pd.DataFrame) else pd.DataFrame()
        payload['overall_investment'] = self.overall_investment_dict.get('overall').copy() if isinstance(self.overall_investment_dict.get('overall'), pd.DataFrame) else pd.DataFrame()
        payload['timestamp'] = time_now

        # 5) compute henery (if WIN available)
        latest_win_odds = None
        latest_win_invest = None
        if not self.odds_dict.get('WIN', pd.DataFrame()).empty:
            latest_win_odds = self.odds_dict['WIN'].tail(1).iloc[0]
        if not self.investment_dict.get('WIN', pd.DataFrame()).empty:
            latest_win_invest = self.investment_dict['WIN'].tail(1).iloc[0]
        payload['henery'] = self.compute_henery_model(latest_win_odds, latest_win_invest)

        # Additionally fetch rankings (non-frequent; do every cycle for parity but it's fine)
        try:
            jockey_df, j_err = fetch_hkjc_jockey_ranking(log_fn=self.log)
            trainer_df, t_err = fetch_hkjc_trainer_ranking(log_fn=self.log)
            payload['jockey_rank'] = jockey_df
            payload['trainer_rank'] = trainer_df
        except Exception as e:
            self.log(f"[cycle] ranking fetch exception: {e}")
            payload['jockey_rank'] = None
            payload['trainer_rank'] = None

        self.log("Emitting data_ready_signal with payload keys: " + ",".join(list(payload.keys())))
        try:
            # emit a deep copy of payload's DataFrames
            safe_payload = {}
            for k, v in payload.items():
                if isinstance(v, pd.DataFrame):
                    safe_payload[k] = v.copy()
                else:
                    safe_payload[k] = v
            self.data_ready_signal.emit(safe_payload)
        except Exception as e:
            self.log(f"[cycle] emit exception: {e}")

    def run(self):
        self.running = True
        self.log(f"Worker started (race_date={self.race_date}, place={self.place}, race_no={self.race_no}, methods={self.methodlist}, delay={self.time_delay})")
        while self.running:
            try:
                self.run_once_cycle()
            except Exception as e:
                self.log(f"[run] cycle outer exception: {e}")
            # Sleep respecting time_delay, but break early if stopped
            for _ in range(int(max(1, self.time_delay))):
                if not self.running:
                    break
                time.sleep(1)
        self.log("Worker stopped loop exit.")
        try:
            self.stopped_signal.emit()
        except Exception:
            pass

    def stop(self):
        self.log("Stop requested.")
        self.running = False
        # wait for thread to stop; leaving join to caller if desired