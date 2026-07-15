import streamlit as st
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from dateutil import relativedelta as datere
import time
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import plotly.graph_objects as go
import os
from warnings import simplefilter
from bs4 import BeautifulSoup
import re
from math import log
from collections import Counter
import plotly.express as px
import itertools
import matplotlib.colors as mcolors
simplefilter(action="ignore", category=pd.errors.PerformanceWarning)

# ==================== 0. 頁面與字型設定 ====================
st.set_page_config(page_title="Jockey Race", layout="wide")

# --- 自動處理中文字型 (專為 Streamlit Cloud 設計) ---
FONT_URL = "https://github.com/googlefonts/noto-cjk/raw/main/Sans/OTF/TraditionalChinese/NotoSansCJKtc-Regular.otf"
FONT_FILE = "NotoSansCJKtc-Regular.otf"

@st.cache_resource
def get_chinese_font():
    # 如果字型檔不存在，則下載
    if not os.path.exists(FONT_FILE):
        with st.spinner("正在下載中文字型 (首次運行需要)..."):
            try:
                r = requests.get(FONT_URL)
                with open(FONT_FILE, "wb") as f:
                    f.write(r.content)
            except:
                st.warning("無法下載中文字型，圖表文字可能顯示為方框。")
                return None
    
    # 加入字型管理器
    if os.path.exists(FONT_FILE):
        fm.fontManager.addfont(FONT_FILE)
        # 設定 Matplotlib 全局字型
        plt.rcParams['font.family'] = fm.FontProperties(fname=FONT_FILE).get_name()
    return FONT_FILE

# 初始化字型
get_chinese_font()

st.title("🏇 Jockey Race 賽馬預測 (Streamlit 版)")

# ==================== 1. Session State 初始化 ====================
def init_session_state():
    defaults = {
        'monitoring': False, # 控制是否正在監控
        'reset': False,
        'odds_dict': {},
        'investment_dict': {},
        'overall_investment_dict': {},
        'weird_dict': {},
        'diff_dict': {},
        'race_dict': {},
        'post_time_dict': {},
        'numbered_list_dict': {},
        'race_dataframes': {},
        'ucb_dict': {},
        'count_history' : {},
        'api_called': False,
        'last_update': None,
        'jockey_ranking_df': pd.DataFrame(),
        'trainer_ranking_df': pd.DataFrame(),
        'top_rank_history': [],
        'top_4_history': [],
        'horse_history': {},
        'high_moneyflow_alerts': pd.DataFrame(columns=["分鐘","時間", "馬號", "當刻賠率", "moneyflow"])
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

init_session_state()

# ==================== 2. 數據下載與處理函數 ====================

def _fetch_graphql_data(operation_name, query, variables):
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
    
    # 使用 Session 保持連線
    session = requests.Session()
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = session.post(url, headers=headers, json=payload, timeout=10)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 403:
                # 處理可能的被封鎖情況，稍微等待
                time.sleep(1)
            else:
                st.warning(f"API 請求失敗 (嘗試 {attempt+1}/{max_retries}): {response.status_code}")
        except Exception as e:
            st.error(f"連線異常: {str(e)}")
        time.sleep(0.5)
    return None

def get_investment_data():
    # 這裡假設 Date, place, race_no, methodlist 已在外部定義 (原程式碼結構)
    # 若是在 Streamlit 內執行，會讀取到全域變數
    variables = {
        "date": str(Date),
        "venueCode": place,
        "raceNo": int(race_no),
        "oddsTypes": methodlist
    }
    
    query = """
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
    
    data = _fetch_graphql_data("racing", query, variables)
    
    investments = {
        "WIN": [], "PLA": [], "QIN": [], "QPL": [],
        "FCT": [], "TRI": [], "FF": []
    }
    
    if data and 'data' in data:
        race_meetings = data['data'].get('raceMeetings', [])
        if race_meetings:
            for meeting in race_meetings:
                pool_invs = meeting.get('poolInvs', [])
                for pool in pool_invs:
                    # 原有的場地過濾邏輯
                    if place not in ['ST','HV']:
                        pool_id = pool.get('id')
                        if pool_id and pool_id[8:10] != place:
                            continue                
                    
                    inv_val = pool.get('investment')
                    if inv_val is not None:
                        try:
                            investments[pool.get('oddsType')].append(float(inv_val))
                        except (ValueError, TypeError):
                            pass
        else:
            # 靜默失敗或記錄日誌，不中斷 Streamlit 介面
            pass
            
    return investments

def get_odds_data():
    variables = {
        "date": str(Date),
        "venueCode": place,
        "raceNo": int(race_no),
        "oddsTypes": methodlist
    }
    
    query = """
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
    
    data = _fetch_graphql_data("racing", query, variables)
    
    odds_values = {
        "WIN": [], "PLA": [], "QIN": [], "QPL": [],
        "FCT": [], "TRI": [], "FF": []
    }
    
    if data and 'data' in data:
        race_meetings = data['data'].get('raceMeetings', [])
        for meeting in race_meetings:
            pm_pools = meeting.get('pmPools', [])
            for pool in pm_pools:
                if place not in ['ST', 'HV']:
                    pool_id = pool.get('id')
                    if pool_id and pool_id[8:10] != place:
                        continue
                
                odds_nodes = pool.get('oddsNodes', [])
                odds_type = pool.get('oddsType')
                
                if not odds_type or odds_type not in odds_values:
                    continue
                
                # 清空該類型的舊資料（原程式碼邏輯）
                odds_values[odds_type] = []
                
                for node in odds_nodes:
                    oddsValue = node.get('oddsValue')
                    if oddsValue == 'SCR':
                        val = np.inf
                    else:
                        try:
                            val = float(oddsValue)
                        except (ValueError, TypeError):
                            continue
                    
                    if odds_type in ["QIN", "QPL", "FCT", "TRI", "FF"]:
                        comb_string = node.get('combString')
                        if comb_string:
                            odds_values[odds_type].append((comb_string, val))
                    else:
                        odds_values[odds_type].append(val)
                        
        # 排序
        for o_type in ["QIN", "QPL", "FCT", "TRI", "FF"]:
            if odds_values[o_type]:
                odds_values[o_type].sort(key=lambda x: x[0])
                
    return odds_values

def fetch_hkjc_jockey_ranking():
    # 目前 2026 年 1 月正處於 2025/26 賽季中期
    season = "25/26" 

    # 1. 完整的 Query Payload (與官方 F12 抓取內容完全一致，不進行任何簡化)
    query = """query rw_GetJockeyRanking($season: String) {
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

    # 官方請求通常包含 operationName
    payload = {
        "operationName": "rw_GetJockeyRanking",
        "variables": {
            "season": season
        },
        "query": query
    }

    # 2. 完整的 Headers (模擬瀏覽器真實環境，防止被攔截)
    headers = {
        "accept": "*/*",
        "accept-language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "content-type": "application/json",
        "priority": "u=1, i",
        "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "Referer": "https://racing.hkjc.com/racing/information/Chinese/Jockey/JockeyRanking.aspx",
        "Origin": "https://racing.hkjc.com",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    }

    try:
        # 執行請求
        response = requests.post(
            "https://info.cld.hkjc.com/graphql/base/", 
            json=payload, 
            headers=headers, 
            timeout=15
        )
        response.raise_for_status()
        result = response.json()

        # 錯誤處理邏輯
        if isinstance(result, list):
            return None, f"API 返回錯誤列表: {result[0].get('message')}"
            
        data = result.get("data")
        if not data:
            error_msg = result.get("errors", [{}])[0].get("message", "Unknown error")
            return None, f"GraphQL 錯誤: {error_msg}"

        jockeys = data.get("jockeyStat", [])
        if not jockeys:
            return None, f"找不到賽季 {season} 的資料 (請確認官方 API 是否變動)"

        rows = []
        for j in jockeys:
            # 解析 ssnStat (這是一個 List)
            ssn_stats = j.get("ssnStat", [])
            
            # 初始化數據容器
            stat_all = {}
            
            # 遍歷列表尋找 trk="ALL" and ven="ALL" (總計數據)
            if isinstance(ssn_stats, list):
                for s in ssn_stats:
                    if s.get("trk") == "ALL" and s.get("ven") == "ALL":
                        stat_all = s
                        break
                
                # 若找不到 ALL，則嘗試抓取第一筆
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
        
        # 數據清理：轉換為數字以便排序
        numeric_cols = ["勝", "亞", "季", "殿", "第五", "出賽", "獎金"]
        df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors='coerce').fillna(0)

        # 計算勝率
        df["勝率 (%)"] = (df["勝"] / df["出賽"].replace(0, 1) * 100).round(1)
        
        # 按照馬會排名規則排序 (勝 > 亞 > 季)
        df = df.sort_values(by=["勝", "亞", "季"], ascending=False).reset_index(drop=True)
        
        # 插入排名欄
        df.insert(0, "排名", df.index + 1)

        return df, None

    except Exception as e:
        return None, f"系統抓取異常: {str(e)}"

def fetch_hkjc_trainer_ranking():
    # 25/26 賽季，嚴格遵循官方格式
    season = "25/26"

    # 完全還原你提供的 Query 字串，不省略任何欄位
    query = """
query rw_GetTrainerRanking($season: String) {
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

    # 嚴格遵循官方的 Payload 結構
    payload = {
        "operationName": "rw_GetTrainerRanking",
        "variables": {
            "season": season
        },
        "query": query
    }

    # 模擬 200 OK 請求所需的完整 Headers
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0",
        "Referer": "https://racing.hkjc.com/racing/information/Chinese/Trainers/TrainerRanking.aspx",
        "Origin": "https://racing.hkjc.com",
        "Accept": "*/*",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site"
    }

    try:
        # 使用你測試成功的 URL
        url = "https://info.cld.hkjc.com/graphql/base/"
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        
        result = resp.json()

        # 檢查 GraphQL 內層是否有錯誤
        if "errors" in result:
            return None, f"GraphQL 錯誤: {result['errors'][0].get('message')}"

        data_section = result.get("data")
        if not data_section:
            return None, "API 回傳 data 欄位為空"

        # 關鍵：針對練馬師，Key 是 trainerStat
        trainers = data_section.get("trainerStat", [])
        if not trainers:
            return None, f"找不到賽季 {season} 的練馬師資料"

        rows = []
        for t in trainers:
            # 解析 ssnStat (List 格式)
            ssn_list = t.get("ssnStat", [])
            target_stat = {}
            
            # 遍歷尋找 trk="ALL" 且 ven="ALL" 的總計數據
            if isinstance(ssn_list, list):
                for s in ssn_list:
                    if s.get("trk") == "ALL" and s.get("ven") == "ALL":
                        target_stat = s
                        break
                # 保底邏輯：如果沒找到 ALL，取列表第一筆
                if not target_stat and len(ssn_list) > 0:
                    target_stat = ssn_list[0]

            # 封裝數據
            rows.append({
                "練馬師": t.get("name_ch", "").strip(),
                "勝": target_stat.get("numFirst", 0),
                "亞": target_stat.get("numSecond", 0),
                "季": target_stat.get("numThird", 0),
                "出賽": target_stat.get("numStarts", 0),
                "獎金": target_stat.get("stakeWon", 0)
            })

        df = pd.DataFrame(rows)
        # 強制轉換數字類型確保後續計算不報錯
        numeric_cols = ["勝", "亞", "季", "出賽", "獎金"]
        df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors='coerce').fillna(0)
        
        return df, None

    except Exception as e:
        return None, f"抓取異常: {str(e)}"
        
def fetch_horse_age_only(date_val, place_val, race_no):
    if place_val in ['ST','HV']:
        base_url = "https://racing.hkjc.com/racing/information/Chinese/racing/RaceCard.aspx?"
        date_str = str(date_val).replace('-', '/')
        url = f"{base_url}RaceDate={date_str}&Racecourse={place_val}&RaceNo={race_no}"
    
        try:
            # 使用同步 requests 取得網頁
            response = requests.get(url, timeout=20)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                # 這是馬會排位表每行馬匹數據的 class
                table_rows = soup.find_all('tr', class_='f_tac f_fs13')
                
                age_data = []
                for row in table_rows:
                    tds = row.find_all('td')
                    if tds[16]:  # 確保索引 16 (馬齡) 存在
                        age_data.append({
                            "編號": tds[0].text.strip(),
                            "馬名": tds[3].text.strip(),
                            "馬齡": tds[16].text.strip()
                        })
                
                # 返回 DataFrame 並設定編號為索引
                return pd.DataFrame(age_data).set_index("編號")
        except Exception as e:
            st.error(f"獲取馬齡失敗: {e}")
            return None


def save_odds_data(time_now,odds):
  for method in methodlist:
      if method in ['WIN', 'PLA']:
        if st.session_state.odds_dict[method].empty:
            # Initialize the DataFrame with the correct number of columns
            st.session_state.odds_dict[method] = pd.DataFrame(columns=np.arange(1, len(odds[method]) + 1))
        st.session_state.odds_dict[method].loc[time_now] = odds[method]
      elif method in ['QIN','QPL',"FCT","TRI","FF"]:
        if odds[method]:
          combination, odds_array = zip(*odds[method])
          if st.session_state.odds_dict[method].empty:
            st.session_state.odds_dict[method] = pd.DataFrame(columns=combination)
            # Set the values with the specified index
          st.session_state.odds_dict[method].loc[time_now] = odds_array
  #st.write(st.session_state.odds_dict)

def save_investment_data(time_now,investment,odds):
  for method in methodlist:
      if method in ['WIN', 'PLA']:
        if st.session_state.investment_dict[method].empty:
            # Initialize the DataFrame with the correct number of columns
            st.session_state.investment_dict[method] = pd.DataFrame(columns=np.arange(1, len(odds[method]) + 1))
        investment_df = [round(investments[method][0]  / 1000 / odd, 2) for odd in odds[method]]
        st.session_state.investment_dict[method].loc[time_now] = investment_df
      elif method in ['QIN','QPL',"FCT","TRI","FF"]:
        if odds[method]:
          combination, odds_array = zip(*odds[method])
          if st.session_state.investment_dict[method].empty:
            st.session_state.investment_dict[method] = pd.DataFrame(columns=combination)
          investment_df = [round(investments[method][0]  / 1000 / odd, 2) for odd in odds_array]
              # Set the values with the specified index
          st.session_state.investment_dict[method].loc[time_now] = investment_df

def investment_combined(time_now,method,df):
  sums = {}
  for col in df.columns:
      # Split the column name to get the numbers
      num1, num2 = col.split(',')
      # Convert to integers
      num1, num2 = int(num1), int(num2)

      # Sum the column values
      col_sum = df[col].sum()

      # Add the sum to the corresponding numbers in the dictionary
      if num1 in sums:
          sums[num1] += col_sum
      else:
          sums[num1] = col_sum

      if num2 in sums:
          sums[num2] += col_sum
      else:
          sums[num2] = col_sum

  # Convert the sums dictionary to a dataframe for better visualization
  sums_df = pd.DataFrame([sums],index = [time_now]) /2
  return sums_df

def get_overall_investment(time_now,dict):
    investment_df = st.session_state.investment_dict
    no_of_horse = len(investment_df['WIN'].columns)
    total_investment_df = pd.DataFrame(index =[time_now], columns=np.arange(1,no_of_horse +1))
    for method in methodlist:
        if method in ['WIN', 'PLA']:
            # Replace _append with pd.concat
            new_data = st.session_state.investment_dict[method].tail(1)
            st.session_state.overall_investment_dict[method] = pd.concat(
                [st.session_state.overall_investment_dict[method], new_data]
            )
            
        elif method in ['QIN', 'QPL']:
            if not investment_df[method].empty:
                # Replace _append with pd.concat
                new_data = investment_combined(time_now, method, st.session_state.investment_dict[method].tail(1))
                st.session_state.overall_investment_dict[method] = pd.concat(
                    [st.session_state.overall_investment_dict[method], new_data]
                )
            else:
                continue

    for horse in range(1,no_of_horse+1):
        total_investment = 0
        for method in methodlist:
            if method in ['WIN', 'PLA']:
                investment = st.session_state.overall_investment_dict[method][horse].values[-1]
            elif method in ['QIN','QPL']:
              if not investment_df[method].empty: 
                investment = st.session_state.overall_investment_dict[method][horse].values[-1]
              else:
                continue
            total_investment += investment
        total_investment_df[horse] = total_investment
    st.session_state.overall_investment_dict['overall'] = pd.concat([st.session_state.overall_investment_dict['overall'], total_investment_df])


def weird_data(time_now, investments, odds, methodlist):
    for method in methodlist:
        if st.session_state.investment_dict[method].empty or len(st.session_state.investment_dict[method]) < 2:
            continue
            
        latest_investment = st.session_state.investment_dict[method].tail(1).values
        # Using previous odds for expectation calculation might be safer, but logic follows user code
        last_time_odds_df = st.session_state.odds_dict[method].tail(2).head(1)
        
        if last_time_odds_df.empty: continue
        last_time_odds = last_time_odds_df.values
        
        try:
            pool_total = investments[method][0]
            expected = pool_total / 1000 / last_time_odds
            # Handling infinity/zero division
            expected = np.where(last_time_odds == np.inf, 0, expected)
            
            diff = np.round(latest_investment - expected, 0)
            diff_df = pd.DataFrame(diff, columns=st.session_state.investment_dict[method].columns, index=[time_now])

            if method in ['WIN','PLA']:
                st.session_state.diff_dict[method] = pd.concat([st.session_state.diff_dict.get(method, pd.DataFrame()), diff_df])
            elif method in ['QIN','QPL']:
                combined_diff = investment_combined(time_now, method, diff_df)
                st.session_state.diff_dict[method] = pd.concat([st.session_state.diff_dict.get(method, pd.DataFrame()), combined_diff])
        except Exception as e:
            # st.error(f"Error in weird_data: {e}")
            pass

def weird_data(investments):
    for method in methodlist:
        if st.session_state.investment_dict[method].empty:
            continue
            
        latest_investment = st.session_state.investment_dict[method].tail(1).values
        last_time_odds = st.session_state.odds_dict[method].tail(2).head(1)
        
        # Calculation logic remains the same
        expected_investment = investments[method][0] / 1000 / last_time_odds
        diff = (latest_investment - expected_investment).round(0)
        
        if method in ['WIN', 'PLA']:
            # Replace _append with pd.concat
            st.session_state.diff_dict[method] = pd.concat(
                [st.session_state.diff_dict[method], diff]
            )
        elif method in ['QIN', 'QPL']:
            # Replace _append with pd.concat
            combined_diff = investment_combined(time_now, method, diff)
            st.session_state.diff_dict[method] = pd.concat(
                [st.session_state.diff_dict[method], combined_diff]
            )
    
def change_overall(time_now):
    total_investment = 0
    for method in methodlist:
        # Summing the diffs for each method
        total_investment += st.session_state.diff_dict[method].sum(axis=0)
    
    # Create the single-row DataFrame for the current time
    total_investment_df = pd.DataFrame([total_investment], index=[time_now])
    
    # Replace _append with pd.concat
    st.session_state.diff_dict['overall'] = pd.concat(
        [st.session_state.diff_dict['overall'], total_investment_df]
    )
# ==================== 3. 繪圖函數 (簡化版) ====================
def print_bar_chart(time_now):
  post_time = st.session_state.post_time_dict[race_no]
  #st.write(post_time)
  #st.write(time_now)  
  time_25_minutes_before = np.datetime64((post_time - timedelta(minutes=25)).replace(tzinfo=None) )
  time_5_minutes_before = np.datetime64((post_time - timedelta(minutes=5)).replace(tzinfo=None))
  
  for method in print_list:
      odds_list = pd.DataFrame()
      df = pd.DataFrame()
      if method == 'overall':
          df = st.session_state.overall_investment_dict[method]
          change_data = st.session_state.diff_dict[method].iloc[-1]
      elif method == 'WIN&QIN':
          df = st.session_state.overall_investment_dict['WIN'] + st.session_state.overall_investment_dict['QIN']
          change_data_1 = st.session_state.diff_dict['WIN'].tail(30).sum(axis = 0) 
          change_data_2 = st.session_state.diff_dict['QIN'].tail(30).sum(axis = 0)
          odds_list = st.session_state.odds_dict['WIN']
      elif method == 'PLA&QPL':
          df = st.session_state.overall_investment_dict['PLA'] + st.session_state.overall_investment_dict['QPL']
          change_data_1 = st.session_state.diff_dict['PLA'].tail(30).sum(axis=0)
          change_data_2 = st.session_state.diff_dict['QPL'].tail(30).sum(axis=0)
          odds_list = st.session_state.odds_dict['PLA']
      elif method in methodlist:
          df = st.session_state.overall_investment_dict[method]
          change_data_1 = st.session_state.diff_dict[method].tail(30).sum(axis = 0)
          change_data_2 = pd.Series(0, index=df.columns)
          odds_list = st.session_state.odds_dict[method]
      if df.empty:
        continue
      fig, ax1 = plt.subplots(figsize=(12, 6))
      df.index = pd.to_datetime(df.index)
      df_1st = pd.DataFrame()
      df_1st_2nd = pd.DataFrame()
      df_2nd = pd.DataFrame()
      #df_3rd = pd.DataFrame()
      df_1st = df[df.index< time_25_minutes_before].tail(1)
      df_1st_2nd = df[df.index >= time_25_minutes_before].head(1)
      df_2nd = df[df.index >= time_25_minutes_before].tail(1)
      df_3rd = df[df.index>= time_5_minutes_before].tail(1)
       
      change_df_1 = pd.DataFrame([change_data_1.apply(lambda x: x*1 if x > 0 else x*1)],columns=change_data_1.index,index =[df.index[-1]])
      change_df_2 = pd.DataFrame([change_data_2.apply(lambda x: x*1 if x > 0 else x*1)],columns=change_data_2.index,index =[df.index[-1]])

      if method in ['WIN', 'PLA', 'WIN&QIN','PLA&QPL']:
        odds_list.index = pd.to_datetime(odds_list.index)
        odds_1st = odds_list[odds_list.index< time_25_minutes_before].tail(1)
        odds_2nd = odds_list[odds_list.index >= time_25_minutes_before].tail(1)
        #odds_3rd = odds_list[odds_list.index>= time_5_minutes_before].tail(1)

      bars_1st = None
      bars_2nd = None
      #bars_3rd = None
      # Initialize data_df
      if not df_1st.empty:
          data_df = df_1st
          data_df = data_df._append(df_2nd)
      elif not df_1st_2nd.empty:
          data_df = df_1st_2nd
          if not df_2nd.empty and not df_2nd.equals(df_1st_2nd):  # Avoid appending identical df_2nd
              data_df = data_df._append(df_2nd)
      else:
          data_df = pd.DataFrame()  # Fallback if both are empty
      #final_data_df = data_df._append(df_3rd)
      final_data_df = data_df
      sorted_final_data_df = final_data_df.sort_values(by=final_data_df.index[0], axis=1, ascending=False)
      diff = sorted_final_data_df.diff().dropna()
      diff[diff < 0] = 0
      X = sorted_final_data_df.columns
      X_axis = np.arange(len(X))
      sorted_change_1 = change_df_1[X]
      sorted_change_2 = change_df_2[X]
      if df_3rd.empty:
                  bar_colour = 'blue'
      else:
                  bar_colour = 'red'
      if not df_1st.empty:
          if df_2nd.empty:
                bars_1st = ax1.bar(X_axis, sorted_final_data_df.iloc[0], 0.4, label='投注額', color='pink')
          else:
                bars_2nd = ax1.bar(X_axis - 0.2, sorted_final_data_df.iloc[1], 0.4, label='25分鐘', color=bar_colour)
                bar = ax1.bar(X_axis+0.2,sorted_change_1.iloc[0],0.4,label='WIN/PLA改變',color='grey')
                if not sorted_change_2.empty:
                    bar = ax1.bar(X_axis+0.2,sorted_change_2.iloc[0].clip(lower=0),0.4,label='QIN/QPL改變',color='green',bottom = sorted_change_1.iloc[0].clip(lower=0))
                    bar = ax1.bar(X_axis+0.2,sorted_change_2.iloc[0].clip(upper=0),0.4,color='green',bottom = sorted_change_1.iloc[0].clip(upper=0))
                    
                #if not df_3rd.empty:
                    #bars_3rd = ax1.bar(X_axis, diff.iloc[0], 0.3, label='5分鐘', color='red')
      else:
            if df_2nd.equals(df_1st_2nd):
              bars_2nd = ax1.bar(X_axis - 0.2, sorted_final_data_df.iloc[0], 0.4, label='25分鐘', color=bar_colour)
            else:
                bars_2nd = ax1.bar(X_axis - 0.2, sorted_final_data_df.iloc[1], 0.4, label='25分鐘', color=bar_colour)
                bar = ax1.bar(X_axis+0.2,sorted_change_1.iloc[0],0.4,label='WIN/PLA改變',color='grey')
                if not sorted_change_2.empty:
                    bar = ax1.bar(X_axis+0.2,sorted_change_2.iloc[0].clip(lower=0),0.4,label='QIN/QPL改變',color='green',bottom = sorted_change_1.iloc[0].clip(lower=0))
                    bar = ax1.bar(X_axis+0.2,sorted_change_2.iloc[0].clip(upper=0),0.4,color='green',bottom = sorted_change_1.iloc[0].clip(upper=0))
                #if not df_3rd.empty:
                    #bars_3rd = ax1.bar(X_axis, diff.iloc[0], 0.3, label='5分鐘', color='red')
            #else:
                #bars_3rd = ax1.bar(X_axis-0.2, sorted_final_data_df.iloc[0], 0.4, label='5分鐘', color='red')
                #bar = ax1.bar(X_axis+0.2,sorted_change_df.iloc[0],0.4,label='改變',color='grey')

      # Add numbers above bars
      if method in ['WIN', 'PLA','WIN&QIN','PLA&QPL']:
        if bars_2nd is not None:
          sorted_odds_list_2nd = odds_2nd[X].iloc[0]
          for bar, odds in zip(bars_2nd, sorted_odds_list_2nd):
              yval = bar.get_height()
              ax1.text(bar.get_x() + bar.get_width() / 2, yval, odds, ha='center', va='bottom')
        #if bars_3rd is not None:
          #sorted_odds_list_3rd = odds_3rd[X].iloc[0]
          #for bar, odds in zip(bars_3rd, sorted_odds_list_3rd):
               # yval = bar.get_height()
                #ax1.text(bar.get_x() + bar.get_width() / 2, yval, odds, ha='center', va='bottom')
        elif bars_1st is not None:
          sorted_odds_list_1st = odds_1st[X].iloc[0]
          for bar, odds in zip(bars_1st, sorted_odds_list_1st):
              yval = bar.get_height()
              ax1.text(bar.get_x() + bar.get_width() / 2, yval, odds, ha='center', va='bottom')
      namelist_raw = st.session_state.race_dataframes[race_no]['馬名']
      namelist_sort = [str(i) + '. ' + str(namelist_raw.iloc[i - 1]) for i in X]
      formatted_namelist = [label.split('.')[0] + '.' + '\n'.join(label.split('.')[1]) for label in namelist_sort]
      
      plt.xticks(X_axis, formatted_namelist, fontsize=16)
      ax1.grid(color='lightgrey', axis='y', linestyle='--')
      ax1.set_ylabel('投注額',fontsize=15)
      ax1.tick_params(axis='y')
      fig.legend()
      HK_TZ = timezone(timedelta(hours=8))
      now_naive = datetime.now()
      now = now_naive + datere.relativedelta(hours=8)
      now = now.replace(tzinfo=HK_TZ)
      post_time_raw = st.session_state.post_time_dict.get(race_no)
            
      if post_time_raw is None:
                time_str = "未載入"
      else:
                # 確保 post_time 也有時區
                if post_time_raw.tzinfo is None:
                    post_time = post_time_raw.replace(tzinfo=HK_TZ)
                else:
                    post_time = post_time_raw  # 已有時區
            
                seconds_left = (post_time - now).total_seconds()
                
                if seconds_left <= 0:
                    time_str = "已開跑"
                else:
                    minutes = int(seconds_left // 60)
                    time_str = f"離開跑 {minutes} 分"  
      if method == 'overall':
          plt.title('綜合', fontsize=15)
      elif method == 'QIN':
          plt.title('連贏', fontsize=15)
      elif method == 'QPL':
          plt.title('位置Q', fontsize=15)
      elif method == 'WIN':
          plt.title('獨贏', fontsize=15)
      elif method == 'PLA':
          plt.title('位置', fontsize=15)
      elif method == 'WIN&QIN':
          plt.title(f'獨贏及連贏 | {time_str}', fontsize=15)
      elif method == 'PLA&QPL':
          plt.title(f'位置及位置Q | {time_str}', fontsize=15)          
      st.pyplot(fig)
def print_bubble(race_no, print_list):
    # 確保有數據
    if 'WIN' not in st.session_state.overall_investment_dict or st.session_state.overall_investment_dict['WIN'].empty:
        return

    for method in print_list:
        if method not in ['WIN&QIN', 'PLA&QPL']: continue
        
        try:
            if method == 'WIN&QIN':
                vol_win = st.session_state.overall_investment_dict.get('WIN', pd.DataFrame())
                vol_qin = st.session_state.overall_investment_dict.get('QIN', pd.DataFrame())
                diff_win = st.session_state.diff_dict.get('WIN', pd.DataFrame())
                diff_qin = st.session_state.diff_dict.get('QIN', pd.DataFrame())
                method_name = ['WIN','QIN']
            else:
                vol_win = st.session_state.overall_investment_dict.get('PLA', pd.DataFrame())
                vol_qin = st.session_state.overall_investment_dict.get('QPL', pd.DataFrame())
                diff_win = st.session_state.diff_dict.get('PLA', pd.DataFrame())
                diff_qin = st.session_state.diff_dict.get('QPL', pd.DataFrame())
                method_name = ['PLA','QPL']

            if vol_win.empty or vol_qin.empty or diff_win.empty or diff_qin.empty:
                continue

            total_volume = vol_win.tail(1) + vol_qin.tail(1)
            # Sum last 10 periods for delta
            delta_I = diff_win.tail(10).sum(axis=0) * 10
            delta_Q = diff_qin.tail(10).sum(axis=0) * 10
            
            df = pd.DataFrame({
                'horse': total_volume.columns.astype(str),
                'ΔI': delta_I.values,
                'ΔQ': delta_Q.values,
                '總投注量': total_volume.iloc[0].fillna(0).round(0).astype(int).values
            })
            
            df = df[df['總投注量'] > 0] # Filter out scratched
            if df.empty: continue

            HK_TZ = timezone(timedelta(hours=8))
            now_naive = datetime.now()
            now = now_naive + datere.relativedelta(hours=8)
            now = now.replace(tzinfo=HK_TZ)
            post_time_raw = st.session_state.post_time_dict.get(race_no)
            
            if post_time_raw is None:
                time_str = "未載入"
            else:
                # 確保 post_time 也有時區
                if post_time_raw.tzinfo is None:
                    post_time = post_time_raw.replace(tzinfo=HK_TZ)
                else:
                    post_time = post_time_raw  # 已有時區
            
                seconds_left = (post_time - now).total_seconds()
                
                if seconds_left <= 0:
                    time_str = "已開跑"
                else:
                    minutes = int(seconds_left // 60)
                    time_str = f"離開跑 {minutes} 分"
            # Normalization for bubble size
            raw_size = df['總投注量']
            bubble_size = 20 + (raw_size - raw_size.min()) / (raw_size.max() - raw_size.min() + 1e-6) * 80
            
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=df['ΔI'], y=df['ΔQ'],
                mode='markers+text',
                text=df['horse'],
                textposition="middle center",
                textfont=dict(color="white", size=22, weight="bold"),
                marker=dict(
                    size=bubble_size,
                    sizemode='area',
                    sizeref=2.*bubble_size.max()/(60**2),
                    color=df['ΔI'],
                    colorscale='Bluered_r',
                    reversescale=True,
                    line=dict(width=1, color='white'),
                    opacity=0.8
                ),
                hovertemplate="<b>馬號：%{text}</b><br>總量：%{customdata:,}K<br>Δ%{yaxis.title.text}: %{y:.1f}K<br>Δ%{xaxis.title.text}: %{x:.1f}K",
                customdata=df['總投注量']
            ))

            fig.add_hline(y=0, line_color="lightgrey")
            fig.add_vline(x=0, line_color="lightgrey")
            fig.update_layout(
                title=f"{method} 氣泡圖 (第{race_no}場) | {time_str}",
                xaxis_title=method_name[0],
                yaxis_title=method_name[1],
                height=500,
                margin=dict(l=20, r=20, t=40, b=20),
                dragmode=False
            )
            st.plotly_chart(fig, width='stretch')
            
        except Exception as e:
            st.error(f"Bubble Chart Error: {e}")
def top(method_odds_df, method_investment_df, method):
    result = {
        "main_table": None,
        "plus_table": None,
        "plus_df": None,
        "notice_table": None
    }
    one_min_no = int (60 / time_delay + 1) 
    third_min_no = int ((one_min_no - 1) * 3 + 1)
    # Extract the first row from odds DataFrame
    first_row_odds = method_odds_df.iloc[0]
    first_row_odds_df = first_row_odds.to_frame(name='Odds').reset_index()
    first_row_odds_df.columns = ['Combination', 'Odds']

    # Extract the last row from odds DataFrame
    last_row_odds = method_odds_df.iloc[-1]
    last_row_odds_df = last_row_odds.to_frame(name='Odds').reset_index()
    last_row_odds_df.columns = ['Combination', 'Odds']
    third_last_row_index = max(-len(method_odds_df), -third_min_no)
    third_last_row_odds = method_odds_df.iloc[third_last_row_index]
    third_last_row_odds_df = third_last_row_odds.to_frame(name='Odds').reset_index()
    third_last_row_odds_df.columns = ['Combination', 'Odds']
    # Extract the second last row from odds DataFrame (or the closest available row)
    second_last_row_index = max(-len(method_odds_df), -one_min_no)
    second_last_row_odds = method_odds_df.iloc[second_last_row_index]
    second_last_row_odds_df = second_last_row_odds.to_frame(name='Odds').reset_index()
    second_last_row_odds_df.columns = ['Combination', 'Odds']

    # Calculate the initial rank and initial odds
    first_row_odds_df['Initial_Rank'] = first_row_odds_df['Odds'].rank(method='min').astype(int)
    first_row_odds_df['Initial_Odds'] = first_row_odds_df['Odds']

    # Calculate the current rank and current odds
    last_row_odds_df['Current_Rank'] = last_row_odds_df['Odds'].rank(method='min').astype(int)
    last_row_odds_df['Initial_Rank'] = first_row_odds_df['Initial_Rank'].values
    last_row_odds_df['Initial_Odds'] = first_row_odds_df['Initial_Odds'].values

    # Calculate the previous rank using the second last row
    second_last_row_odds_df['Previous_Rank'] = second_last_row_odds_df['Odds'].rank(method='min').astype(int)
    last_row_odds_df['Previous_Rank'] = second_last_row_odds_df['Previous_Rank'].values

    # Calculate the change of rank
    last_row_odds_df['Change_of_Rank'] = last_row_odds_df['Initial_Rank'] - last_row_odds_df['Current_Rank']
    last_row_odds_df['Change_of_Rank'] = last_row_odds_df['Change_of_Rank'].apply(lambda x: f'+{x}' if x > 0 else (str(x) if x < 0 else '0'))

    # Combine the initial rank and change of rank into the same column format like 10 (+1)
    last_row_odds_df['Initial_Rank'] = last_row_odds_df.apply(lambda row: f"{row['Initial_Rank']}" f"({row['Change_of_Rank']})", axis=1)

    # Calculate the difference between the current rank and previous rank and add this difference to the previous rank in the format 10 (+1)
    last_row_odds_df['Change_of_Previous_Rank'] = last_row_odds_df['Previous_Rank'] - last_row_odds_df['Current_Rank']
    last_row_odds_df['Change_of_Previous_Rank'] = last_row_odds_df['Change_of_Previous_Rank'].apply(lambda x: f'+{x}' if x > 0 else (str(x) if x < 0 else '0'))
    last_row_odds_df['Previous_Rank'] = last_row_odds_df.apply(lambda row: f"{row['Previous_Rank']}" f"({row['Change_of_Previous_Rank']})", axis=1)

    # Rearrange the columns as requested
    final_df = last_row_odds_df[['Combination', 'Odds', 'Initial_Odds', 'Current_Rank', 'Initial_Rank', 'Previous_Rank']]

    # Format the odds to one decimal place using .loc to avoid SettingWithCopyWarning
    final_df.loc[:, 'Odds'] = final_df['Odds'].round(1)
    final_df.loc[:, 'Initial_Odds'] = final_df['Initial_Odds'].round(1)

    # Extract the first row from investment DataFrame
    first_row_investment = method_investment_df.iloc[0]
    first_row_investment_df = first_row_investment.to_frame(name='Investment').reset_index()
    first_row_investment_df.columns = ['Combination', 'Investment']

    # Extract the last row from investment DataFrame
    last_row_investment = method_investment_df.iloc[-1]
    last_row_investment_df = last_row_investment.to_frame(name='Investment').reset_index()
    last_row_investment_df.columns = ['Combination', 'Investment']

    # Extract the second last row from investment DataFrame (or the closest available row)
    second_last_row_index = max(-len(method_investment_df), -one_min_no)
    second_last_row_investment = method_investment_df.iloc[second_last_row_index]
    second_last_row_investment_df = second_last_row_investment.to_frame(name='Investment').reset_index()
    second_last_row_investment_df.columns = ['Combination', 'Investment']
    third_last_row_index = max(-len(method_investment_df), -third_min_no)
    third_last_row_investment = method_investment_df.iloc[third_last_row_index]
    third_last_row_investment_df = third_last_row_investment.to_frame(name='Investment').reset_index()
    third_last_row_investment_df.columns = ['Combination', 'Investment']
    # Calculate the difference in investment before sorting
    last_row_investment_df['Investment_Change'] = last_row_investment_df['Investment'] - first_row_investment_df['Investment'].values
    last_row_investment_df['Investment_Change'] = last_row_investment_df['Investment_Change'].apply(lambda x: x if x > 0 else 0)
    second_last_row_investment_df['Previous_Investment_Change'] = last_row_investment_df['Investment'] - second_last_row_investment_df['Investment'].values
    second_last_row_investment_df['Previous_Investment_Change'] = second_last_row_investment_df['Previous_Investment_Change'].apply(lambda x: x if x > 0 else 0)
    third_last_row_investment_df['Previous_Investment_Change'] = last_row_investment_df['Investment'] - third_last_row_investment_df['Investment'].values
    third_last_row_investment_df['Previous_Investment_Change'] = third_last_row_investment_df['Previous_Investment_Change'].apply(lambda x: x if x > 0 else 0)

    # Sort the final DataFrame by odds value
    final_df = final_df.sort_values(by='Odds')

    # Combine the investment data with the final DataFrame based on the combination
    final_df = final_df.merge(last_row_investment_df[['Combination', 'Investment_Change', 'Investment']], on='Combination', how='left')
    final_df = final_df.merge(second_last_row_investment_df[['Combination', 'Previous_Investment_Change']], on='Combination', how='left')
    final_df = final_df.merge(third_last_row_investment_df[['Combination', 'Previous_Investment_Change']], on='Combination', how='left')

    if method in ['WIN','PLA']:
        final_df.columns = ['馬匹', '賠率', '最初賠率', '排名', '最初排名', '上一次排名', '投注變化', '投注', '一分鐘投注','三分鐘投注']
        target_df = final_df
        rows_with_plus = target_df[
              target_df['最初排名'].astype(str).str.contains(r'\+') |
              target_df['上一次排名'].astype(str).str.contains(r'\+')
        ][['馬匹', '賠率', '最初排名', '上一次排名']]
          # Apply the conditional formatting to the 初始排名 and 前一排名 columns and add a bar to the 投資變化 column
        styled_df = final_df.style.format({
            '賠率': '{:.1f}',
            '最初賠率': '{:.1f}',
            '投注變化': '{:.2f}k',
            '投注': '{:.2f}k',
            '一分鐘投注': '{:.2f}k',
            '三分鐘投注': '{:.2f}k'
          }).map(highlight_change, subset=['最初排名', '上一次排名']).bar(subset=['投注變化', '一分鐘投注','三分鐘投注'], color='rgba(173, 216, 230, 0.5)').hide(axis='index')
        styled_rows_with_plus = rows_with_plus.style.format({'賠率': '{:.1f}'}).map(highlight_change, subset=['最初排名', '上一次排名']).hide(axis='index')
          # Display the styled DataFrame
        result["main_table"] = styled_df
        result["plus_table"] = styled_rows_with_plus 
        result["plus_df"] = target_df
      #st.write(styled_df.to_html(), unsafe_allow_html=True)
      #st.write(styled_rows_with_plus.to_html(), unsafe_allow_html=True)


    else:
        final_df.columns = ['組合', '賠率', '最初賠率', '排名', '最初排名', '上一次排名', '投注變化', '投注', '一分鐘投注','三分鐘投注']
        target_df = final_df.head(15)
        target_special_df = final_df.head(50)
        rows_with_plus = target_special_df[
              target_special_df['最初排名'].astype(str).str.contains(r'\+') |
              target_special_df['上一次排名'].astype(str).str.contains(r'\+')
        ][['組合', '賠率', '最初排名', '上一次排名', '一分鐘投注','三分鐘投注']]
        
    
          # Apply the conditional formatting to the 初始排名 and 前一排名 columns and add a bar to the 投資變化 column
        styled_df = target_df.style.format({
            '賠率': '{:.1f}',
            '最初賠率': '{:.1f}',
            '投注變化': '{:.2f}k',
            '投注': '{:.2f}k',
            '一分鐘投注': '{:.2f}k',
            '三分鐘投注': '{:.2f}k'
        }).map(highlight_change, subset=['最初排名', '上一次排名']).bar(subset=['投注變化', '一分鐘投注','三分鐘投注'], color='rgba(173, 216, 230, 0.5)').hide(axis='index')
        styled_rows_with_plus = rows_with_plus.style.format({
            '賠率': '{:.1f}',
            '一分鐘投注': '{:.2f}k',
            '三分鐘投注': '{:.2f}k'
        }).bar(subset=['一分鐘投注', '三分鐘投注'], color='rgba(173, 216, 230, 0.5)').map(highlight_change, subset=['最初排名', '上一次排名']).hide(axis='index')
          # Display the styled DataFrame
        result["main_table"] = styled_df
        result["plus_table"] = styled_rows_with_plus  
        result["plus_df"] = final_df
      #st.write(styled_df.to_html(), unsafe_allow_html=True)
        notice_df = None  
        if method in ["QIN","QPL","FCT","TRI","FF"]:
            if method in ["QIN"]:
              notice_df = final_df[(final_df['一分鐘投注'] >= 100) | (final_df['三分鐘投注'] >= 300)][['組合', '賠率', '一分鐘投注', '三分鐘投注']]
            elif method in ["QPL"]:
              notice_df = final_df[(final_df['一分鐘投注'] >= 200) | (final_df['三分鐘投注'] >= 600)][['組合', '賠率', '一分鐘投注', '三分鐘投注']]
            elif method in ["FCT"]:
              notice_df = final_df[(final_df['一分鐘投注'] >= 10) | (final_df['三分鐘投注'] >= 30)][['組合', '賠率', '一分鐘投注', '三分鐘投注']]
            else:
              notice_df = final_df[(final_df['一分鐘投注'] >= 5) | (final_df['三分鐘投注'] >= 15)][['組合', '賠率', '一分鐘投注', '三分鐘投注']]
        if notice_df is not None:
            styled_notice_df = notice_df.style.format({'賠率': '{:.1f}','一分鐘投注': '{:.2f}k','三分鐘投注': '{:.2f}k'}).bar(subset=['一分鐘投注','三分鐘投注'], color='rgba(173, 216, 230, 0.5)').hide(axis='index')
        result["notice_table"] = styled_notice_df  

    return result
      #col1, col2 = st.columns(2)
      #with col1:
        #st.write(styled_rows_with_plus.to_html(), unsafe_allow_html=True)
      #with col2:
        #st.write(styled_notice_df.to_html(), unsafe_allow_html=True)

def print_top():
  for method in top_list:
        tables = top(st.session_state.odds_dict[method], st.session_state.investment_dict[method], method)
        if tables["main_table"]:
            st.write(tables["main_table"].to_html(), unsafe_allow_html=True)
        if tables["plus_table"] or tables["notice_table"]:
            col1, col2 = st.columns(2)
            with col1:
                if tables["plus_table"]:
                    st.write(tables["plus_table"].to_html(), unsafe_allow_html=True)
            with col2:
                if tables["notice_table"]:
                    st.write(tables["notice_table"].to_html(), unsafe_allow_html=True)
                    
def highlight_change(val):
    color = 'limegreen' if '+' in val else 'crimson' if '-' in val else ''
    return f'color: {color}'

import plotly.graph_objects as go
import pandas as pd
from datetime import datetime, timedelta

def print_plotly_advanced_bar(race_no, method): # 建議傳入 method 區分
    # 1. 取得對應數據 (這裡以 WIN/QIN 為例，你可以根據 method 調整)
    # 假設你的 method 分別是 'WIN&QIN' 或 'PLA&QPL'
    for method in print_list:
        # --- 1. 數據源判斷與提取 ---
        if method == 'WIN&QIN':
            df_base, df_top = st.session_state.overall_investment_dict['WIN'], st.session_state.overall_investment_dict['QIN']
            diff_base, diff_top = st.session_state.diff_dict['WIN'], st.session_state.diff_dict['QIN']
            odds_df = st.session_state.odds_dict['WIN']
            label_base, label_top = "WIN", "QIN"
        elif method == 'PLA&QPL':
            df_base, df_top = st.session_state.overall_investment_dict['PLA'], st.session_state.overall_investment_dict['QPL']
            diff_base, diff_top = st.session_state.diff_dict['PLA'], st.session_state.diff_dict['QPL']
            odds_df = st.session_state.odds_dict['PLA']
            label_base, label_top = "PLA", "QPL"
        elif method == 'PLA':
            df_base = st.session_state.overall_investment_dict['PLA']
            df_top = pd.DataFrame(0, index=df_base.index, columns=df_base.columns)
            diff_base = st.session_state.diff_dict['PLA']
            diff_top = pd.DataFrame(0, index=diff_base.index, columns=diff_base.columns)
            odds_df = st.session_state.odds_dict['PLA']
            label_base, label_top = "PLA", ""
    
        all_ts = df_base.index
        data_len = len(all_ts)
        if data_len < 1: return
    
        # --- 2. 準備馬名與排序 (以最新數據為準固定 X 軸) ---
        current_total = (df_base + df_top).iloc[-1]
        sorted_cols = current_total.sort_values(ascending=False).index
        namelist_raw = st.session_state.race_dataframes[race_no]['馬名']
        horse_labels = []
        for c in sorted_cols:
            name = namelist_raw.iloc[c-1]
            # 讓馬名垂直排列：每個字中間加 <br>
            vertical_name = "<br>".join(list(name))
            horse_labels.append(f"{c}.<br>{vertical_name}")
        post_time = st.session_state.post_time_dict[race_no].replace(tzinfo=None)
    
        # --- 3. 預先計算所有動畫幀 (Frames) ---
        frames = []
        for i, ts in enumerate(all_ts):
            ts_raw = ts.replace(tzinfo=None)
            time_diff = (post_time - ts_raw).total_seconds() / 60
            
            # 根據該幀的時間決定顏色
            if time_diff <= 5: 
                current_frame_color = 'rgb(255, 99, 132)'   # 紅 (5分內)
                show_diff = True
            elif time_diff <= 25: 
                current_frame_color = 'rgb(54, 162, 235)'   # 藍 (5-25分)
                show_diff = True
            else: 
                current_frame_color = 'rgb(255, 205, 210)' # 粉 (>25分)
                show_diff = False
    
            # 建立該幀的數據圖層
            frame_data = [
                go.Bar(
                    x=horse_labels, 
                    y=(df_base + df_top).iloc[i][sorted_cols], 
                    marker_color=current_frame_color, # ⬅️ 確保顏色被寫入這一幀
                    offsetgroup=1, 
                    text=odds_df.iloc[i][sorted_cols], 
                    textposition='outside', 
                    name='總投注'
                )
            ]
            
            # 25分內才顯示變動棒
            if time_diff <= 25:
                start_idx = max(0, i - 9)
                raw_c_base = diff_base.iloc[start_idx:i+1].sum(axis=0)[sorted_cols]
                raw_c_top = diff_top.iloc[start_idx:i+1].sum(axis=0)[sorted_cols]
                
                # --- 關鍵：執行放大邏輯 (正數 * 6, 負數 * 3) ---
                def amplify(val):
                    return val * 6 if val > 0 else val * 3
    
                amp_c_base = raw_c_base.apply(amplify)
                amp_c_top = raw_c_top.apply(amplify)
                frame_data.append(go.Bar(x=horse_labels, y=amp_c_base, marker_color='grey', offsetgroup=2, name=f'{label_base}變'))
                if method != 'PLA':
                    frame_data.append(go.Bar(x=horse_labels, y=amp_c_top, marker_color='green', offsetgroup=2, base=amp_c_base, name=f'{label_top}變'))
    
            frames.append(go.Frame(data=frame_data, name=ts.strftime("%H:%M:%S")))
    
            # --- 4. 配置佈局與 Plotly 滑塊 ---
            fig = go.Figure(
            data=frames[-1].data,
            layout=go.Layout(
                title=f"{method} 數據回溯",
                barmode='group',
                dragmode=False,
                # 1. 顯著增加高度 (例如從 500 改為 700 或 800)
                height=850, 
                
                # 2. 移除不必要的空白邊距，讓圖表充滿畫布
                # t (top), b (bottom), l (left), r (right)
                margin=dict(l=20, r=20, t=60, b=350), 
                
                xaxis={
                    'fixedrange': True,
                    'tickangle': 0,      # 既然馬名已經垂直處理，角度設為 0
                    'automargin': True,  # 強制自動補償標籤高度
                    'tickfont': {'size': 14}
                },
                yaxis={
                    'fixedrange': True,
                    # 確保金額不會被切掉
                    'automargin': True 
                },
                
                # 3. 圖例位置優化 (放在頂部，不佔用側面寬度)
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.02,
                    xanchor="right",
                    x=1
                ),
    
                # 4. 滑塊配置
                sliders=[{
                    "active": data_len - 1,
                    "currentvalue": {"prefix": "時間: ", "offset": 30},
                    "pad": {"t": 180},
                    "steps": [
                        {
                            # 關鍵：redraw 設為 True，確保顏色切換能被渲染
                            "args": [[f.name], {"frame": {"duration": 0, "redraw": True}, "mode": "immediate"}],
                            "label": f.name,
                            "method": "animate",
                        } for f in frames
                    ]
                }]
            ),
            frames=frames
        )
    
        # 5. 使用 use_container_width=True 讓圖表隨網頁寬度自動撐滿
        latest_ts = all_ts[-1].strftime("%H%M%S")
        st.plotly_chart(fig, width='stretch', key=f"fluent_{race_no}_{method}_{latest_ts}")
def get_rank_font_colors(series):
    """
    對應原本的 highlight_change 邏輯：
    '+' -> limegreen (綠色)
    '-' -> crimson (紅色)
    其餘 -> white (白色)
    """
    colors = []
    for val in series:
        val_str = str(val)
        if '+' in val_str:
            colors.append('limegreen')
        elif '-' in val_str:
            colors.append('crimson')
        else:
            colors.append('white') # 預設顏色
    return colors
def print_henery_model(gamma=1.18):
    """
    Henery Model 完整實作版
    解決問題：
    1. 1號馬缺失 (不再使用 iloc[1:])
    2. 雙位數馬匹 (10, 11, 12) 匹配失敗
    3. 格式相容性 (支援 "02,10", "2-10", "2.0,10.0" 等格式)
    """
    # --- 1. 時間處理與合併顯示 ---
    HK_TZ = timezone(timedelta(hours=8))
    now = datetime.now(HK_TZ)
    
    # 獲取開跑倒數
    post_time_raw = st.session_state.post_time_dict[race_no]
    if post_time_raw:
        post_time = post_time_raw.replace(tzinfo=HK_TZ) if post_time_raw.tzinfo is None else post_time_raw
        seconds_left = (post_time - now).total_seconds()
        time_str = "🏁 已開跑" if seconds_left <= 0 else f"⏳ 離開跑 {int(seconds_left // 60)} 分"
    else:
        time_str = "未載入"

    # 獲取最後更新時間
    last_upd = st.session_state.last_update.strftime('%H:%M:%S') if st.session_state.get('last_update') else "N/A"
    
    # 合併顯示在一句 Markdown 中
    st.markdown(f"#### {time_str} ｜ 📟 數據最後同步: `{last_upd}`")

    # --- 2. 數據合法性檢查 ---
    if 'odds_dict' not in st.session_state: return
    win_df = st.session_state.odds_dict.get('WIN')
    qin_df = st.session_state.odds_dict.get('QIN')
    if win_df is None or qin_df is None or len(win_df) == 0: return

    # --- 3. 處理 WIN (以整數作為馬號 Key) ---
    latest_win = win_df.iloc[-1]
    win_probs, win_odds_map = {}, {}
    inv_sum = 0
    
    for col, odds in latest_win.items():
        try:
            # 關鍵：馬號統一存成整數 2, 10
            h_num = int(float(str(col).strip()))
            val = pd.to_numeric(odds, errors='coerce')
            if val > 0 and val != np.inf and not pd.isna(val):
                win_probs[h_num] = 1.0 / val
                win_odds_map[h_num] = val
                inv_sum += 1.0 / val
        except: continue
            
    if inv_sum == 0: return
    for h in win_probs: win_probs[h] /= inv_sum

    # --- 4. 處理 QIN (最強力模糊解析) ---
    latest_qin = qin_df.iloc[-1]
    actual_qin = {}
    
    for comb_col, odds in latest_qin.items():
        val = pd.to_numeric(odds, errors='coerce')
        if val > 0 and not pd.isna(val):
            # 使用正則表達式抓取所有數字，無視 "02,10" 中的 0 或逗號
            nums = re.findall(r'\d+', str(comb_col))
            if len(nums) == 2:
                # 關鍵：轉成整數後排序，確保 (2, 10) 永遠是 (2, 10)
                n1, n2 = int(nums[0]), int(nums[1])
                key = tuple(sorted([n1, n2])) 
                actual_qin[key] = val

    # --- 5. Henery 計算 ---
    results = []
    # 按馬號大小排序
    horses = sorted(win_probs.keys())
    
    for h1, h2 in itertools.combinations(horses, 2):
        p1, p2 = win_probs[h1], win_probs[h2]
        denom1 = sum(win_probs[h]**gamma for h in horses if h != h1)
        denom2 = sum(win_probs[h]**gamma for h in horses if h != h2)
        p_qin = (p1 * (p2**gamma / denom1)) + (p2 * (p1**gamma / denom2))
        theo_odds = 1.0 / p_qin
        
        # 精確整數匹配： (2, 10)
        a_odds = actual_qin.get((h1, h2))
        if a_odds:
            val_score = a_odds / theo_odds
            results.append({
                "組合": f"{h1}-{h2}",
                #"馬1獨贏": win_odds_map[h1],
                #"馬2獨贏": win_odds_map[h2],
                "實時Q": a_odds,
                "理論Q": round(theo_odds, 1),
                "Value": round(val_score, 2)
            })

    tables = top(st.session_state.odds_dict["QIN"], st.session_state.investment_dict["QIN"], "QIN")
    plus_df = tables.get("plus_df")
    plus_df_clean = plus_df.copy()
    plus_df_clean = plus_df_clean[['組合', '排名','最初排名', '上一次排名']]
    if plus_df_clean is not None and not plus_df_clean.empty:
        # --- 關鍵步驟：格式化 plus_df 的組合名稱 ---
        # 假設 plus_df['組合'] 是 "01,02" 或 "1, 2"，統一轉成 "1-2"
        def normalize_comb(comb_str):
            nums = re.findall(r'\d+', str(comb_str))
            if len(nums) == 2:
                n1, n2 = sorted([int(nums[0]), int(nums[1])])
                return f"{n1}-{n2}"
            return comb_str
    plus_df_clean['組合'] = plus_df_clean['組合'].apply(normalize_comb)
    def get_table_html(df, cmap_name):
        return (
            df.style.background_gradient(subset=['Value'], cmap=cmap_name)
            .format({"實時Q": "{:.1f}", "理論Q": "{:.1f}", "Value": "{:.2f}"})
            .hide(axis='index').map(highlight_change, subset=['最初排名', '上一次排名'])
            # This CSS ensures headers don't wrap and the table fills the width
            .set_table_attributes('style="width:100%; border-collapse: collapse; white-space: nowrap;"')
            .to_html()
        )
      
    # --- 6. 渲染雙表格介面 ---
    if results:
        full_df = pd.DataFrame(results)
        full_df = pd.merge(full_df, plus_df_clean, on='組合', how='left')
        full_df = full_df[['組合', '排名','最初排名', '上一次排名','實時Q','理論Q','Value']]
        full_df = full_df[full_df["實時Q"] < 100]
        col1, col2 = st.columns(2)
    
        #with col1:
           # st.success("✅ **高價值組合 (Value > 1.1)**")
           # high_df = full_df[full_df["Value"] > 1.1].sort_values("實時Q", ascending=False).head(25).sort_values("Value", ascending=True)
            #if not high_df.empty:
                #st.markdown(get_table_html(high_df, 'Greens'), unsafe_allow_html=True)
            #else:
                #st.info("目前無符合條件組合")
    
        
        st.error("🔥 **過熱組合 (Value < 0.9)**")
        overheated_df = full_df[full_df["Value"] < 0.9].sort_values("實時Q", ascending=True).head(25)
        #.sort_values("Value", ascending=True)
        if not overheated_df.empty:
            st.markdown(get_table_html(overheated_df, 'Reds_r'), unsafe_allow_html=True)
        else:
            st.info("目前無過熱組合")
        # --- 7. 最終優化版：支援系統 Dark Mode + 左對齊 ---
        ov_df = full_df[full_df["Value"] < 0.9].copy()
            
        # 獲取場中所有馬號（即使沒過熱也顯示按鈕）
        all_horse_list = sorted(list(win_probs.keys()))
        num_horses = len(all_horse_list)
        fig = go.Figure()
        buttons = []

        for i, h_num in enumerate(all_horse_list):
            mask = ov_df['組合'].apply(lambda x: any(int(part) == h_num for part in x.split('-')))
            sub_df = ov_df[mask].sort_values("Value").reset_index(drop=True)

            if not sub_df.empty:
                # 🌈 同時取得背景與字體顏色
                val_bg_colors, val_font_colors = get_adaptive_colors(sub_df["Value"])
                init_font_colors = get_rank_font_colors(sub_df["最初排名"])
                prev_font_colors = get_rank_font_colors(sub_df["上一次排名"])
                fig.add_trace(
                    go.Table(
                        columnwidth = [100, 80, 80, 80, 80, 100],
                        header=dict(
                            values=["<b>組合</b>", "<b>排名</b>","<b>最初</b>", "<b>上一次</b>", "<b>實時Q</b>", "<b>理論Q</b>", "<b>Value</b>"],
                            fill_color='#111111', align='center', font=dict(color='white',size = 18),
                            line_color='#333333'
                        ),
                        cells=dict(
                            values=[sub_df["組合"],sub_df["排名"], sub_df["最初排名"], sub_df["上一次排名"], 
                                    sub_df["實時Q"], sub_df["理論Q"], sub_df["Value"]],
                            fill_color=[
                                ['rgba(30,30,30,0.5)']*len(sub_df),
                                ['rgba(30,30,30,0.5)']*len(sub_df),
                                ['rgba(30,30,30,0.5)']*len(sub_df),
                                ['rgba(30,30,30,0.5)']*len(sub_df),
                                ['rgba(30,30,30,0.5)']*len(sub_df),
                                ['rgba(30,30,30,0.5)']*len(sub_df),
                                val_bg_colors  # 背景漸層
                            ],
                            font=dict(
                                color=[
                                    ['white']*len(sub_df), # 其他欄位固定白字
                                    ['white']*len(sub_df),
                                    init_font_colors,
                                    prev_font_colors,
                                    ['white']*len(sub_df),
                                    ['white']*len(sub_df),
                                    val_font_colors        # ⬅️ Value 字體動態黑白切換
                                ],
                                size=18,
                            ),
                            align='center', line_color='#333333',height=45
                        ),
                        visible=(i == 0),
                        domain=dict(x=[0, 1.0])
                    )
                )
            else:
                # --- 無組合的提示表格 ---
                fig.add_trace(
                    go.Table(
                        header=dict(
                            values=["<b>狀態提示</b>"], 
                            fill_color='#111111', font=dict(color='white')
                        ),
                        cells=dict(
                            values=[[f"馬匹 {h_num} 目前沒有過熱組合"]], 
                            fill_color=['rgba(30,30,30,0.5)'], 
                            font=dict(color='#888888', size=20), height=60
                        ),
                        visible=(i == 0),
                        domain=dict(x=[0, 1.0])
                    )
                )

            # 按鈕列表
            buttons_per_row = 7
            row_count = (num_horses + buttons_per_row - 1) // buttons_per_row
            menu_list = []
            
            for row_idx in range(0, num_horses, buttons_per_row):
                row_horses = all_horse_list[row_idx : row_idx + buttons_per_row]
                row_buttons = []
                
                for h_btn in row_horses:
                    mask = ov_df['組合'].apply(lambda x: any(int(part) == h_btn for part in x.split('-')))
                    sub_ov = ov_df[mask]
                    count = len(ov_df[mask])
                    plus_count = 0
                    if not sub_ov.empty and '上一次排名' in sub_ov.columns:
                        plus_count = sub_ov['上一次排名'].astype(str).str.contains(r'\+').sum()
                        
                    g_idx = all_horse_list.index(h_btn)
                    
                    # 建立 visibility 陣列：只有點擊的那匹馬對應的 Trace 是 True
                    # 其餘全部（包含其他行的馬）都是 False
                    vis = [False] * num_horses
                    vis[g_idx] = True
                    
                    # 這裡我們不依賴系統的 active 顏色
                    row_buttons.append(dict(
                        label=f" {h_btn}號</b><br> ({count}) (+{plus_count})",
                        method="update",
                        # 當點擊時，我們更新 Trace 的可見性，並可以順便更新 Layout 標題作為提示
                        args=[{"visible": vis}, {"title": f"<b>正在檢視：{h_btn} 號馬過熱組合</b>"}]
                    ))
                current_row_from_bottom = row_count - 1 - (row_idx // buttons_per_row)
                menu_list.append(dict(
                    type="buttons",
                    direction="right",
                    x=0, 
                    xanchor="left",
                    yanchor="bottom",
                    # ⬇️ 這裡改為 1.01，按鈕就會直接坐在表格頂線上
                    y=1.01 + (current_row_from_bottom * 0.08), 
                    buttons=row_buttons,
                    showactive=False,
                    bgcolor="#333333",
                    font=dict(color="white", size=15),
                    bordercolor="#555555",
                    borderwidth=1,
                    pad={"r": 8, "t": 2, "b": 0} 
                ))
    
            # --- 2. 修正 Layout (壓縮頂部空間讓表格上移) ---
            fig.update_layout(
                dragmode=False,
                updatemenus=menu_list,
                # ⬇️ 關鍵：t 不能太小（否則按鈕會出界），但也不能太大（否則表格會下沉）
                # 建議設為 30 + (行數 * 35)，這樣能確保按鈕剛好頂到最上方，表格跟著上移
                margin=dict(t=30 + (row_count * 35), b=10, l=0, r=0), 
                height=650,
                paper_bgcolor='rgba(0,0,0,0)',
                font=dict(color="white", family="Arial")
            )

        st.plotly_chart(
            fig, 
            width='stretch', 
            key=f"dark_left_table_{race_no}_{time_now.strftime('%H%M%S')}", 
            config={'displayModeBar': False}
        )
        
        # --- 8. 新增：全寬熱力圖趨勢 ---
        st.markdown("---") # 分割線
        st.subheader("🔥 歷史熱度掃描 (Heatmap)")
        if win_df is not None:
            # 排除非馬號的 column (如果有)，並排序
            latest_win_series = win_df.iloc[-1]
            current_horses = sorted([c for c in win_df.columns if str(c).isdigit()], key=lambda x: int(x))
            full_horse_list = sorted(current_horses, key=lambda h: pd.to_numeric(latest_win_series.get(h, 999), errors='coerce'))
            full_horse_list = [str(h) for h in full_horse_list]
            sync_time = win_df.index[-1]
        else:
            full_horse_list = [str(h) for h in sorted(win_probs.keys())]
        # 確保 session_state 存在
        if 'horse_count_history' not in st.session_state:
            st.session_state.horse_count_history = {}
       
        y_labels_filtered = []
        
        # 這裡放入前面討論的數據採集與繪圖代碼
        current_time = datetime.now(HK_TZ).strftime('%H:%M:%S')
        current_horse_counts = {str(h): 0 for h in all_horse_list}
        
        for h_num in all_horse_list:
            mask = ov_df['組合'].apply(lambda x: any(int(part) == h_num for part in x.split('-')))
            current_horse_counts[str(h_num)] = len(ov_df[mask])
    
        if race_no not in st.session_state.horse_count_history:
            st.session_state.horse_count_history[race_no] = pd.DataFrame()
    
        hist_df = st.session_state.horse_count_history[race_no]
        new_entry = pd.DataFrame([current_horse_counts], index=[sync_time])
        
        if hist_df.empty or current_time != hist_df.index[-1]:
            updated_hist = pd.concat([hist_df, new_entry]).tail(40)
            st.session_state.horse_count_history[race_no] = updated_hist
    
        # 渲染圖表
        plot_data = st.session_state.horse_count_history[race_no]
        if not plot_data.empty:
            # 只顯示有過熱記錄的馬，避免 14 匹馬太多空白
            active_cols = [c for c in plot_data.columns if plot_data[c].iloc[-1] >2]
            
            if active_cols:
                active_cols = [h for h in full_horse_list if h in active_cols]
                z_df = plot_data[active_cols].T.iloc[::-1] # 轉置讓 Y 軸是馬號
                # 3. 從 win_df 抽取對應的賠率矩陣 (Text 軸)
                # 確保 win_df 的 columns 與 active_cols 格式一致 (處理 int/str 差異)
                # win_col_keys = [int(c) if int(c) in win_df.columns else str(c) for c in active_cols]
                # 直接抽取這幾匹馬的所有歷史賠率，並對齊熱力圖的時間點 (z_df.columns)
                # reindex 會自動處理時間對齊，若 win_df 漏了某秒會補 NaN
                # odds_sub_df = win_df[win_col_keys].reindex(z_df.columns).T.iloc[::-1]
                # 將 NaN 轉為 0 方便顯示，並轉為 values 給 Plotly
                # raw_matrix = odds_sub_df.fillna(0).values
                # clean_text_matrix = []
                # for row in raw_matrix:
                    # new_row = []
                    # last_val = None
                    # for i, val in enumerate(row):
                        # 邏輯：如果是第一格，或者數值跟上一格不同，就顯示數字
                        # if i == 0 or val != last_val:
                            # new_row.append(f"{val:.1f}")
                        # else:
                            # 數值相同則留空，減少視覺壓力
                            # new_row.append("")
                        # last_val = val
                    # clean_text_matrix.append(new_row)
                    
                y_labels_filtered = []
                latest_win = win_df.iloc[-1] if win_df is not None else None
                prev_win = (win_df.iloc[-4] if len(win_df) >= 4 else win_df.iloc[0]) if win_df is not None else None
                prev_3_win = (win_df.iloc[-10] if len(win_df) >= 10 else win_df.iloc[0]) if win_df is not None else None
                for h_str in active_cols:
                    col_key = int(h_str) if win_df is not None and int(h_str) in win_df.columns else h_str
                    
                    if latest_win is not None and col_key in latest_win:
                        curr_o = pd.to_numeric(latest_win[col_key], errors='coerce')
                        prev_o = pd.to_numeric(prev_win[col_key], errors='coerce')
                        prev_3_o = pd.to_numeric(prev_3_win[col_key], errors='coerce')
                        #diff =  prev_o - curr_o
                        #arrow = "▼" if diff < 0 else "▲" if diff > 0 else ""
                        #diff_color = "#00ff00" if diff > 0 else "#ff4b4b" if diff < 0 else "#888"
                        
                        label = (
                            f"<b>{int(h_str):02d} 號</b> <span>{curr_o:.1f}</span> <br>"
                            f"<span style='color:#888; font-size:20px'>({prev_o:.1f})</span> <span style='color:#888; font-size:20px'>(({prev_3_o:.1f}))</span></b>"
                            #f"<span style='color:{diff_color}; font-size:14px'><b>{arrow} {abs(diff):.1f}</b></span>"
                        )#style='color:#fff'
                    else:
                        label = f"<b>{int(h_str):02d} 號</b><br>-" #<br>-"
                    y_labels_filtered.append(label)
                y_labels_rich = y_labels_filtered[::-1]
                fixed_zmin = 2
                #fixed_zmax = z_df.values.max()
                colorscale_thresholds = [
                    [0, '#FFFFFF'],       # 0: 純白 (背景)
                    [0.1, '#FFEEEE'],     # 1: 極淡粉紅
                    [0.2, '#FFFF99'],     # 2: 淺黃
                    [0.4, '#FFFF00'],     # 3-4: 亮黃
                    [0.6, '#FF9999'],     # 5-6: 淺紅
                    [0.8, '#FF0000'],     # 7-9: 正紅
                    [1.0, '#330066']      # 10+: 深紫 (焦點)
                ]
                fig_heat = go.Figure(data=go.Heatmap(
                    z=z_df.values,
                    x=z_df.columns,
                    y=y_labels_rich,
                    # text=clean_text_matrix,
                    # texttemplate="%{text:.1f}",
                    # textfont={"size": 20},
                    ygap=2.5,
                    zmin=fixed_zmin,      
                    #zmax=fixed_zmax,
                    colorscale=colorscale_thresholds, # 深黑到鮮紅
                    showscale=True,
                    zauto=False,
                    colorbar=dict(title="過熱數")
                ))
                dynamic_height = 150 + (len(full_horse_list) * 25)
                fig_heat.update_layout(
                    height=dynamic_height, # 動態高度
                    width = 1500,
                    margin=dict(t=10, b=10, l=100, r=10),
                    paper_bgcolor='rgba(0,0,0,0)',
                    plot_bgcolor='rgba(0,0,0,0)',
                    dragmode=False,
                    font=dict(color="white"),
                    xaxis=dict(showticklabels=False, showgrid=False,zeroline=False, fixedrange=True),
                    #xaxis=dict(showgrid=False, tickangle=-45,fixedrange=True),
                    yaxis=dict(showgrid=False, title="馬號",fixedrange=True,tickfont=dict(size=20))
                )
                st.plotly_chart(fig_heat, width='content')
        
        return full_df # 最後回傳完整 DataFrame
    
    return pd.DataFrame()
    
def get_adaptive_colors(values, cmap_name='Reds_r'):
    """
    回傳背景色列表與對應的字體顏色列表 (黑或白)
    """
    if len(values) == 0: return [], []
    
    cmap = plt.get_cmap(cmap_name)
    norm = mcolors.Normalize(vmin=0.2, vmax=1.0) 
    
    bg_colors = []
    font_colors = []
    
    for v in values:
        # 1. 取得背景 RGBA
        rgba = cmap(norm(v))
        bg_hex = mcolors.to_hex(rgba)
        bg_colors.append(bg_hex)
        
        # 2. 計算亮度 (Luminance) 演算法
        # 公式: 0.299*R + 0.587*G + 0.114*B
        r, g, b, _ = rgba
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        
        # 3. 根據亮度決定字體顏色 (閾值通常設為 0.5)
        font_colors.append('white' if luminance < 0.5 else '#31333F')
        
    return bg_colors, font_colors
    
def plot_racing_monitor_dashboard():
    """
    整合賠率與投注量監控，併排顯示兩張獨立圖表
    """
    # 1. 檢查數據來源
    if 'odds_dict' not in st.session_state or 'overall_investment_dict' not in st.session_state:
        st.warning("數據加載中，請稍候...")
        return

    # 取得數據
    df_odds = st.session_state.odds_dict.get('WIN', pd.DataFrame())
    inv_dict = st.session_state.overall_investment_dict
    
    if df_odds.empty:
        st.info("暫無賠率數據")
        return

    # ---------------------------------------------------------
    # 2. 核心排序邏輯 (兩圖統一按賠率排序)
    # ---------------------------------------------------------
    latest_odds = df_odds.iloc[-1].sort_values()
    sorted_horses = latest_odds.index.tolist()
    top_6_horses = sorted_horses[:6]
    
    # 統一顏色序列
    colors = px.colors.qualitative.Dark24
    def get_horse_color(horse_name):
        # 根據馬號固定顏色，避免排名變動時顏色亂跳
        try:
            return colors[int(horse_name) % len(colors)]
        except:
            return "#FFFFFF"

    # ---------------------------------------------------------
    # 3. 繪製賠率圖 (Odds Chart)
    # ---------------------------------------------------------
    fig_odds = go.Figure()
    for horse in sorted_horses:
        is_top_6 = horse in top_6_horses
        fig_odds.add_trace(go.Scatter(
            x=df_odds.index, y=df_odds[horse],
            name=f"{horse} 號",
            mode='lines+markers',
            marker=dict(size=4),
            visible=True if is_top_6 else "legendonly",
            line=dict(width=3 if is_top_6 else 2, color=get_horse_color(horse)),
            hovertemplate=f"馬號 {horse}<br>賠率: %{{y:.1f}}<extra></extra>"
        ))

    fig_odds.update_layout(
        title="📉 獨贏賠率 (熱門在上)",
        template="plotly_dark",
        yaxis=dict(type='log',  tickformat=".1f", dtick=0.301, gridcolor='rgba(255,255,255,0.1)'), #autorange='reversed',
        xaxis=dict(showgrid=True, gridcolor='rgba(255,255,255,0.05)'),
        dragmode=False, hovermode="x unified",
        legend=dict(itemclick="toggle", itemdoubleclick="toggleothers", traceorder="normal"),
        height=600, margin=dict(t=80, b=50, l=60, r=20)
    )

    # ---------------------------------------------------------
    # 4. 繪製金額圖 (Investment Chart)
    # ---------------------------------------------------------
    # 合併 WIN 與 QIN 投注額 (假設馬號是相同的 Index)
    df_win_inv = pd.DataFrame(inv_dict.get("WIN", {}))
    df_qin_inv = pd.DataFrame(inv_dict.get("QIN", {}))
    # 這裡假設你是想加總同一隻馬在不同池的表現，或者是對比
    df_total_inv = df_win_inv.add(df_qin_inv, fill_value=0) if not df_win_inv.empty else df_qin_inv

    fig_inv = go.Figure()
    # 保持與賠率圖「完全相同」的馬匹順序添加 Trace，讓 Legend 對齊
    for horse in sorted_horses:
        if horse not in df_total_inv.columns: continue
        is_top_6 = horse in top_6_horses
        
        fig_inv.add_trace(go.Scatter(
            x=df_total_inv.index, y=df_total_inv[horse],
            name=f"{horse} 號",
            mode='lines+markers',
            marker=dict(size=4),
            visible=True if is_top_6 else "legendonly",
            line=dict(width=3 if is_top_6 else 1.5, color=get_horse_color(horse)),
            hovertemplate=f"馬號 {horse}<br>金額: %{{y:,.0f}}<extra></extra>"
        ))

    fig_inv.update_layout(
        title="💰 投注量走勢 (對齊賠率排序)",
        template="plotly_dark",
        yaxis=dict(side='right', tickformat=",.0f", gridcolor='rgba(255,255,255,0.1)'),
        xaxis=dict(showgrid=True, gridcolor='rgba(255,255,255,0.05)'),
        dragmode=False, hovermode="x unified",
        legend=dict(itemclick="toggle", itemdoubleclick="toggleothers", traceorder="normal"),
        height=600, margin=dict(t=80, b=50, l=20, r=60)
    )

    # ---------------------------------------------------------
    # 5. Streamlit Layout 併排顯示
    # ---------------------------------------------------------
    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(fig_odds, width='content', config={'displayModeBar': False})
    #with c2:
        #st.plotly_chart(fig_inv, width='stretch', config={'displayModeBar': False})

# ==================== 4. 主介面邏輯 ====================

# --- 輸入區 ---
with st.sidebar:
    st.header("設定")
    Date = st.date_input('日期:', value=datetime.now(timezone(timedelta(hours=8))).date())
    place = st.selectbox('場地:', ['ST', 'HV', 'S1', 'S2', 'S3' , 'S4', 'S5'])
    race_no = st.selectbox('場次:', np.arange(1, 12))
    
    st.markdown("---")
    st.subheader("監控選項")
    
    # 監控開關
    monitoring_on = st.toggle("啟動即時監控", value=False)
    keep_keys = ["show_bubble", "show_bar", "show_move_bar", "show_top", "show_henery","bar_key", "bubble_key"]
    if st.button("重置所有數據"):
        for key in list(st.session_state.keys()):
            if key not in keep_keys:
                del st.session_state[key]
        st.rerun()
        
    show_bubble = st.toggle("📍 顯示氣泡圖", key="show_bubble", value=False)
    show_bar = st.toggle("📊 顯示長條圖", key="show_bar", value=False)
    show_move_bar = st.toggle("📊 顯示移動長條圖", key="show_move_bar", value=True)
    show_top = st.toggle("🏆 顯示連贏賠率排名", key="show_top", value=True)
    show_henery = st.toggle("🚀 顯示Henery Model 預測", key="show_henery", value=True)
# --- 賽事資料加載 ---
@st.cache_data(ttl=3600)
def fetch_race_card(date_str, venue):
    # 這是一個簡化的 RaceCard 抓取，只抓基本資料以顯示
    # 完整邏輯較長，這裡保留核心概念：抓取馬名與基本資料
    url = 'https://info.cld.hkjc.com/graphql/base/'
    headers = {'Content-Type': 'application/json'}
    payload = {
        "operationName": "raceMeetings",
        "variables": {"date": date_str, "venueCode": venue},
        "query": """
      fragment raceFragment on Race {
        id
        no
        status
        raceName_en
        raceName_ch
        postTime
        country_en
        country_ch
        distance
        wageringFieldSize
        go_en
        go_ch
        ratingType
        raceTrack {
          description_en
          description_ch
        }
        raceCourse {
          description_en
          description_ch
          displayCode
        }
        claCode
        raceClass_en
        raceClass_ch
        judgeSigns {
          value_en
        }
      }
  
      fragment racingBlockFragment on RaceMeeting {
        jpEsts: pmPools(
          oddsTypes: [TCE, TRI, FF, QTT, DT, TT, SixUP]
          filters: ["jackpot", "estimatedDividend"]
        ) {
          leg {
            number
            races
          }
          oddsType
          jackpot
          estimatedDividend
          mergedPoolId
        }
        poolInvs: pmPools(
          oddsTypes: [WIN, PLA, QIN, QPL, CWA, CWB, CWC, IWN, FCT, TCE, TRI, FF, QTT, DBL, TBL, DT, TT, SixUP]
        ) {
          id
          leg {
            races
          }
        }
        penetrometerReadings(filters: ["first"]) {
          reading
          readingTime
        }
        hammerReadings(filters: ["first"]) {
          reading
          readingTime
        }
        changeHistories(filters: ["top3"]) {
          type
          time
          raceNo
          runnerNo
          horseName_ch
          horseName_en
          jockeyName_ch
          jockeyName_en
          scratchHorseName_ch
          scratchHorseName_en
          handicapWeight
          scrResvIndicator
        }
      }
  
      query raceMeetings($date: String, $venueCode: String) {
        timeOffset {
          rc
        }
        activeMeetings: raceMeetings {
          id
          venueCode
          date
          status
          races {
            no
            postTime
            status
            wageringFieldSize
          }
        }
        raceMeetings(date: $date, venueCode: $venueCode) {
          id
          status
          venueCode
          date
          totalNumberOfRace
          currentNumberOfRace
          dateOfWeek
          meetingType
          totalInvestment
          country {
            code
            namech
            nameen
            seq
          }
          races {
            ...raceFragment
            runners {
              id
              no
              standbyNo
              status
              name_ch
              name_en
              horse {
                id
                code
              }
              color
              barrierDrawNumber
              handicapWeight
              currentWeight
              currentRating
              internationalRating
              gearInfo
              racingColorFileName
              allowance
              trainerPreference
              last6run
              saddleClothNo
              trumpCard
              priority
              finalPosition
              deadHeat
              winOdds
              jockey {
                code
                name_en
                name_ch
              }
              trainer {
                code
                name_en
                name_ch
              }
            }
          }
          obSt: pmPools(oddsTypes: [WIN, PLA]) {
            leg {
              races
            }
            oddsType
            comingleStatus
          }
          poolInvs: pmPools(
            oddsTypes: [WIN, PLA, QIN, QPL, CWA, CWB, CWC, IWN, FCT, TCE, TRI, FF, QTT, DBL, TBL, DT, TT, SixUP]
          ) {
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
          ...racingBlockFragment
          pmPools(oddsTypes: []) {
            id
          }
          jkcInstNo: foPools(oddsTypes: [JKC], filters: ["top"]) {
            instNo
          }
          tncInstNo: foPools(oddsTypes: [TNC], filters: ["top"]) {
            instNo
          }
        }
      }
      """
  }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        if response.status_code == 200:
            data = response.json()
            races = data.get('data', {}).get('raceMeetings', [])
            race_info = {}
            for meeting in races:
                for race in meeting.get('races', []):
                    r_no = race['no']
                    runners = race.get('runners', [])
                    #st.write(runners)
                    # 關鍵修改：過濾後備馬匹 (standbyNo 為空字串或 None)
                    filtered_runners = [r for r in runners if not r.get('standbyNo')]

                    data_list = []
                    for r in filtered_runners:
                        
                        # --- 關鍵修正：將字串評分轉換為整數 ---
                        try:
                            # 讀取字串並轉換為整數 (int("059") -> 59)
                            rating_val = int(r.get('currentRating', '0'))
                        except (ValueError, TypeError):
                            rating_val = 0
                            
                        # 排位和負磅也同樣進行穩健的數字轉換
                        try:
                            draw_val = int(r.get('barrierDrawNumber', '0'))
                        except (ValueError, TypeError):
                            draw_val = 0

                        try:
                            weight_val = int(r.get('handicapWeight', '0'))
                        except (ValueError, TypeError):
                            weight_val = 0
                        data_list.append({
                            "馬號": r['no'],
                            "馬名": r['name_ch'],
                            "騎師": r['jockey']['name_ch'] if r['jockey'] else '',
                            "練馬師": r['trainer']['name_ch'] if r['trainer'] else '',
                            "近績": r.get('last6run', ''),
                            
                            # 使用轉換後的數值
                            "評分": rating_val,
                            "排位": draw_val,
                            "負磅": weight_val
                        })

                    df = pd.DataFrame(data_list)
                    if not df.empty:
                        # 將馬號轉換為數字並排序，確保順序正確
                        df['馬號_int'] = pd.to_numeric(df['馬號'], errors='coerce')
                        df = df.sort_values("馬號_int").drop(columns=['馬號_int']).set_index("馬號")
                    df_age = fetch_horse_age_only(date_str, venue, r_no)
                    if df_age is not None and not df_age.empty:
                        # 使用馬號索引進行左連接 (Left Join)
                        # df_age 的索引需要是馬號，對應 df 的索引
                        df = df.join(df_age[['馬齡']], how='left')
                    else:
                        # 如果抓不到馬齡，補上空值欄位避免後續計算報錯
                        df['馬齡'] = ""
                    # Post Time
                    pt_str = race.get("postTime")
                    pt = datetime.fromisoformat(pt_str) if pt_str else None
                    
                    race_info[r_no] = {"df": df, "post_time": pt}
            return race_info
    except Exception as e:
        st.error(e)
    return {}

def fetch_race_card_oversea(date_val, place_val,race_no):
        date_str = str(date_val).replace('-', '')
        headers = {
            'accept': '*/*',
            'accept-language': 'en-us,en;q=0.9',
            'content-type': 'application/json',
            'origin': 'https://racing.hkjc.com',
            'priority': 'u=1, i',
            'referer': 'https://racing.hkjc.com/',
            'sec-ch-ua': '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-site',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
        }
        
        json_data = {
            'variables': {
                'date': str(date_val),
                'venueCode': str(place_val),
                'type': 'LIEF_TIME',
                'meetingDate': date_str,
                'raceNumber': str(race_no),
                'venCode': str(place_val),
            },
            'query': '\nquery RaceCardProfile($date: String, $venueCode: String, $type: STStatType, $ids: [String!], $raceNumber: String, $meetingDate: String) {\n  raceMeetingProfile(date: $date, venueCode: $venueCode) {\n    totalNumberOfRace\n    status\n    pmPools {\n      leg {\n        races\n      }\n      status\n      oddsType\n    }\n    races {\n      id\n      no\n      status\n      postTime\n      raceName_en\n      raceName_ch\n      raceResults {\n        status\n      }\n      countryCodeNm {\n        code\n        english\n        chinese\n      }\n      distance\n      raceCourse {\n        code\n        description_en\n        description_ch\n      }\n      raceTrack {\n        code\n        description_en\n        description_ch\n      }\n      raceType_en\n      raceType_ch\n      raceClass_en\n      raceClass_ch\n      country_en\n      country_ch\n      winningMargin {\n        seqNo\n        lbw\n      }\n      go_en\n      go_ch\n      remarks {\n        name_en\n        name_ch\n        seqNo\n      }\n      runners {\n        horse {\n          name_en\n          name_ch\n          id\n        }\n        status\n        color\n        no\n        handicapWeight\n        jockey {\n          code\n          name_en\n          name_ch\n        }\n        trainer {\n          code\n          name_en\n          name_ch\n        }\n        id\n        last6run\n        internationalRating\n        currentRating \n        sire\n        sexNm {\n          chinese\n          english\n          code\n        }\n        age\n        barrierDrawNumber\n        gearInfo\n        stat(type: $type) {\n          statType\n          numStarts\n          numFirst\n          numSecond\n          numThird\n        }\n        damNm {\n          code\n          chinese\n          english\n        }\n        sireOfDamNm {\n          code\n          chinese\n          english\n        }\n        ownerNm {\n          code\n          chinese\n          english\n        }\n        colorNm {\n          code\n          chinese\n          english\n        }\n      }\n    }\n    date\n    venueCode\n  }\n\n  simulcastHorse(ids: $ids, raceNumber: $raceNumber, meetingDate: $meetingDate, venCode: $venueCode) {\n    id\n    brandNumber\n    earings\n    performanceStats {\n      type\n      firstPlace\n      secondPlace\n      thirdPlace\n      totalRun\n      ssn\n    } \n  }\n}\n',
        }
        
        
        try:
            response = requests.post('https://info.cld.hkjc.com/graphql/base/', headers=headers, json=json_data)

            if response.status_code == 200:
                res_json = response.json()
            # 1. 深入資料層級
            # 這裡假設 variables 傳入的是特定場次，races 通常會是一個列表
                data = res_json.get('data', {})
                profile_list = data.get('raceMeetingProfile', [])
                race_info = {}
                # 注意：races 是 [ ] 列表，所以這裡不能接著 .get('runners')
                for profile in profile_list:
                    # 現在的 profile 是字典了，可以使用 .get()
                    races_list = profile.get('races', [])
                    for race in races_list:
                        runners = race.get('runners', [])
                        r_no = race['no']
                        data_list = []
                        for r in runners:
                            # 模仿你的邏輯：抓取 編號、馬名、馬齡
                            h = r.get('horse', {})
                            rating_val = int(r.get('currentRating')) if r.get('currentRating') else 0
                            draw_val = int(r.get('barrierDrawNumber')) if r.get('barrierDrawNumber') else 0
                            weight_val = int(r.get('handicapWeight')) if r.get('handicapWeight') else 0
                            data_list.append({
                                "馬號": str(r.get('no', '')),
                                "馬名": h.get('name_ch', ''),
                                "馬齡": str(r.get('age', '')),
                                "騎師": r['jockey']['name_ch'] if r.get('jockey') else '',
                                "練馬師": r['trainer']['name_ch'] if r.get('trainer') else '',
                                "近績": r.get('last6run', ''),
                                "評分": rating_val,
                                "排位": draw_val,
                                "負磅": weight_val
                            })
                        df = pd.DataFrame(data_list)
                        if not df.empty:
                            # 將馬號轉換為數字並排序，確保順序正確
                            df['馬號_int'] = pd.to_numeric(df['馬號'], errors='coerce')
                            df = df.sort_values("馬號_int").drop(columns=['馬號_int']).set_index("馬號")
                        # Post Time
                        pt_str = race.get("postTime")
                        pt = datetime.fromisoformat(pt_str) if pt_str else None

                        race_info[r_no] = {"df": df, "post_time": pt}
                    # 返回 DataFrame 並設定編號為索引
                return race_info
        except Exception as e:
            st.error(f"解析發生錯誤: {e}")

def parse_form_score(last6run_str):
    """
    將 '1/2/4/11/2' 這樣的字串轉換為實力分數 (0-100)
    名次越小分數越高。
    """
    if not last6run_str or last6run_str == '-': return 50
    
    ranks = []
    # 處理如 "1/2/3" 或 "1 2 3" 的格式
    parts = re.split(r'[/ ]', str(last6run_str))
    for p in parts:
        p = p.strip()
        if p.isdigit(): ranks.append(int(p))
        elif p == '10': ranks.append(10)
        elif p in ['UR', 'FE', 'DISQ']: ranks.append(14) # 意外視為最後

    if not ranks: return 50
    
    # 只取最近 4 場，權重：0.4, 0.3, 0.2, 0.1
    ranks = ranks[:4]
    weights = [0.4, 0.3, 0.2, 0.1][:len(ranks)]
    # 歸一化權重
    weights = [w / sum(weights) for w in weights]
    
    weighted_rank = sum(r * w for r, w in zip(ranks, weights))
    
    # 趨勢獎勵：如果最近一場比前一場好
    bonus = 0
    if len(ranks) >= 2:
        if ranks[0] < ranks[1]: bonus += 5
        if ranks[0] <= 3: bonus += 5 # 進入前三名獎勵
        
    score = 100 - (weighted_rank - 1) * 7.5 + bonus
    return max(0, min(100, score))

def calculate_jockey_score(jockey_name, ranking_df):
    """
    計算騎師評分
    """
    # 錯誤代碼 51: DataFrame 為空或未定義
    if ranking_df is None or not isinstance(ranking_df, pd.DataFrame) or ranking_df.empty:
        return 54.0

    # 處理輸入名稱
    target_name = str(jockey_name).strip()
    
    # 使用 str.contains 進行模糊搜尋，na=False 防止 NaN 導致崩潰
    # 加入 regex=False 提高效能並防止名稱中含特殊字元
    jockey_row = ranking_df[ranking_df['騎師'].str.contains(target_name, na=False, regex=False)]
    
    # 錯誤代碼 52: 找不到該騎師
    if jockey_row.empty:
        return 52.0

    # 修正：使用對應的欄位名稱 '勝' 與 '出賽'
    wins = jockey_row['勝'].iloc[0]
    runs = jockey_row['出賽'].iloc[0]
    
    # 錯誤代碼 53: 出賽數為 0
    if runs == 0:
        return 53.0
    
    # 計算該騎師勝率
    win_rate = wins / runs
    
    # 取得全港最高勝率作為基準 (篩選出賽超過 10 次的騎師，避免 1 戰 1 勝這種極端值)
    bench_df = ranking_df[ranking_df['出賽'] > 10].copy()
    
    if not bench_df.empty:
        # 計算基準勝率
        bench_df['wr'] = bench_df['勝'] / bench_df['出賽']
        max_rate = bench_df['wr'].max()
    else:
        max_rate = 0.20 # 預設基準
    
    # 確保 max_rate 不為 0
    max_rate = max(max_rate, 0.01)
    
    # 計算分數 (0-100)，並限制最小分數為 15 分
    score = (win_rate / max_rate) * 100
    return round(min(max(score, 15), 100), 1)


def calculate_trainer_score(trainer_name, trainer_df):
    """
    計算練馬師評分
    """
    # 51: 數據表為空
    if trainer_df is None or trainer_df.empty:
        return 54.0

    target_name = str(trainer_name).strip()
    # 模糊匹配
    row = trainer_df[trainer_df['練馬師'].str.contains(target_name, na=False, regex=False)]
    
    # 52: 找不到該人
    if row.empty:
        return 52.0

    wins = row['勝'].iloc[0]
    runs = row['出賽'].iloc[0]
    
    # 53: 出賽數為 0
    if runs == 0:
        return 53.0
    
    win_rate = wins / runs
    
    # 基準勝率 (排除出賽太少的練馬師)
    bench_df = trainer_df[trainer_df['出賽'] > 10].copy()
    if not bench_df.empty:
        bench_df['wr'] = bench_df['勝'] / bench_df['出賽']
        max_rate = bench_df['wr'].max()
    else:
        max_rate = 0.15 # 練馬師勝率通常比頂尖騎師低一點，給個合理的預設
    
    max_rate = max(max_rate, 0.01)
    
    score = (win_rate / max_rate) * 100
    return round(min(max(score, 15), 100), 1)
def calculate_smart_score(race_no):
    """
    計算單場賽事的綜合評分，並將所有中間結果整合到單一 df。
    """
    
    # ----------------------------------------------------
    # I. 數據準備與初始 df 建立
    # ----------------------------------------------------
    
    # 1. 獲取最新賠率 (Odds)
    if 'WIN' not in st.session_state.odds_dict or st.session_state.odds_dict['WIN'].empty:
        return pd.DataFrame()
        
    latest_odds = st.session_state.odds_dict['WIN'].tail(1).T
    latest_odds.columns = ['Odds']
    
    # 2. 獲取資金流向 (MoneyFlow)
    # 建立一個基礎的 DataFrame，索引與 latest_odds 一致，初始值為 0
    total_money_flow = pd.DataFrame(0, index=latest_odds.index, columns=['MoneyFlow'])
    total_overall_flow = pd.DataFrame(0, index=latest_odds.index, columns=['OverallMoneyFlow'])
    for method in methodlist:
        # 檢查該種類是否存在於 session_state 且不為空
        if method in st.session_state.diff_dict and not st.session_state.diff_dict[method].empty:
            # 提取最近 10 筆數據並加總
            # .sum() 會根據欄位加總，確保索引對齊
            current_method_sum = st.session_state.diff_dict[method].tail(10).sum()
            all_time_sum = st.session_state.diff_dict[method].sum()
            # 將加總後的數據加到總表中 (使用 add 函數可以自動處理索引不匹配的情況)
            total_money_flow['MoneyFlow'] = total_money_flow['MoneyFlow'].add(current_method_sum, fill_value=0)
            total_overall_flow['OverallMoneyFlow'] = total_overall_flow['OverallMoneyFlow'].add(all_time_sum, fill_value=0)
    # 最後得到的 money_flow 就是四個種類加總後的結果
    money_flow = total_money_flow
        
    # 3. 建立基礎 df (包含動態數據)
    df = pd.concat([latest_odds, money_flow, total_overall_flow], axis=1)
    
    # 4. 獲取靜態數據
    if race_no not in st.session_state.race_dataframes:
        return pd.DataFrame()
        
    # 我們只需要 '馬號' 和計算分數所需的欄位
    static_df = st.session_state.race_dataframes[race_no].copy()
    
    # ----------------------------------------------------
    # II. 索引標準化 (確保合併成功)
    # ----------------------------------------------------
    
    # 確保 static_df 以 '馬號' 作為索引
    if static_df.index.name != '馬號':
        static_df = static_df.reset_index().set_index('馬號')
        
    # **關鍵步驟：強制將兩個 DataFrame 的索引類型統一為字串**
    try:
        df.index = df.index.astype(str)
        static_df.index = static_df.index.astype(str)
    except Exception as e:
        st.error(f"索引轉換錯誤: {e}")
        return pd.DataFrame()
        
    # ----------------------------------------------------
    # III. 靜態數據分數計算 (在 static_df 上計算)
    # ----------------------------------------------------
    
    # 檢查並補齊必要的欄位
    required_cols = ['近績', '評分', '排位'] # 只需要計算所需欄位
    for col in required_cols:
        if col not in static_df.columns:
            static_df[col] = 0
            
    # 1. 狀態分數 (Form Score) - 權重 40%
    static_df['FormScore'] = static_df['近績'].apply(parse_form_score)
    
    # 2. 騎師分數 (Jockey Score) - 權重 15% (取代部分 Synergy)
    j_df, j_err = fetch_hkjc_jockey_ranking()
    t_df, t_err = fetch_hkjc_trainer_ranking()
    static_df['JockeyScore'] = static_df['騎師'].apply(
        lambda x: calculate_jockey_score(str(x).strip(), j_df)
    )
    
    # 練馬師分數 (15%)
    static_df['TrainerScore'] = static_df['練馬師'].apply(
        lambda x: calculate_trainer_score(str(x).strip(), t_df)
    )
    
    # 3. 適應性分數 (Draw Score) - 權重 20%
    static_df['排位_int'] = pd.to_numeric(static_df['排位'], errors='coerce').fillna(99)
    static_df['DrawScore'] = 100 - (static_df['排位_int'] - 1) * (100 / 13) 
    
    # 4. 負擔分數 (Rating Score) - 權重 10%
    static_df['Rating_int'] = pd.to_numeric(static_df['評分'], errors='coerce').fillna(0)
    max_rating = static_df['Rating_int'].replace(0, np.nan).max() # 避免 max_rating 為 0
    
    if pd.isna(max_rating):
        static_df['RatingDiffScore'] = 50
    else:
        static_df['RatingDiffScore'] = (static_df['Rating_int'] / max_rating) * 100 
    
    # 最終靜態加權公式
    static_df['TotalFormScore'] = (static_df['FormScore'] * 0.4) + \
                                  (static_df['JockeyScore'] * 0.15) + \
                                  (static_df['TrainerScore'] * 0.15) + \
                                  (static_df['DrawScore'] * 0.2) + \
                                  (static_df['RatingDiffScore'] * 0.1)
    
    # ----------------------------------------------------
    # IV. 使用 join/merge 將靜態分數整合到 df (達成單一 df 目的)
    # ----------------------------------------------------
    
    # 只取出計算好的分數欄位
    static_scores = static_df[['馬名','馬齡','騎師','排位','練馬師','TotalFormScore', 'FormScore', 'JockeyScore','TrainerScore', 'DrawScore', 'RatingDiffScore']]
    
    # 使用 join 進行合併：左連接，以 df 的馬號為準。
    # 由於索引已統一為字串，join 將正確地按馬號匹配。
    df = df.join(static_scores, how='left')
    df['顯示名稱'] = df.index.astype(str) + ". " + df['馬名'].fillna("未知")
    # 如果有馬匹在靜態數據中找不到 (例如 TotalFormScore 為 NaN)，則填入預設值
    df['TotalFormScore'] = df['TotalFormScore'].fillna(50) 
    
    # ----------------------------------------------------
    # V. 在單一 df 上計算最終綜合得分 (TotalScore)
    # ----------------------------------------------------
    
    # A. 資金分數 (MoneyScore)
    min_flow = df['MoneyFlow'].min()
    max_flow = df['MoneyFlow'].max()
    
    # 避免 MoneyFlow 都是 0 時除以 0
    if max_flow != min_flow:
        df['MoneyScore'] = (df['MoneyFlow'] - min_flow) / (max_flow - min_flow) * 100
    else:
        df['MoneyScore'] = 50
        
    # B. 價值分數 (ValueScore: 隱含勝率/熱度)
    # 避免 Odds 為 0 或 NaN 時除以 0
    df['ValueScore'] = np.where(df['Odds'].replace(0, np.nan).isna(), 0, (1 / df['Odds']) * 100)
    
    # C. 最終加權公式 (實力 30% + 資金流向 50% + 賠率熱度 20%)
    df['TotalScore'] = (df['TotalFormScore'] * 0.3) + \
                       (df['MoneyScore'] * 0.5) + \
                       (df['ValueScore'] * 0.2)
    df.loc[np.isinf(df['Odds']), 'TotalScore'] = 0                        
    return df.sort_values('TotalScore', ascending=False)
    
def calculate_smart_score_static(race_no):
    """
    核心預測算法（靜態版）：專為比賽前一日，缺乏賠率和資金流數據時設計。
    權重：狀態 (40%) + 配搭 (30%) + 適應性 (20%) + 負擔 (10%)
    """
    if race_no not in st.session_state.race_dataframes:
        return pd.DataFrame()
    
    static_df = st.session_state.race_dataframes[race_no].copy()
    
    # 確保所有馬匹都有一個馬號索引
    if static_df.index.name != '馬號':
        static_df = static_df.reset_index().set_index('馬號')

    # 檢查關鍵欄位是否存在 (如果沒有，需要先在 fetch_race_card 中獲取)
    required_cols = ['近績', '評分', '排位', '騎師', '練馬師']
    for col in required_cols:
        if col not in static_df.columns:
            # 這是為了兼容，但建議您去 fetch_race_card 補齊這些欄位
            static_df[col] = 0 
            
    # 1. 狀態分數 (Form Score) - 權重 40%
    # 使用原有的 parse_form_score
    static_df['FormScore'] = static_df['近績'].apply(parse_form_score)
    
    # 2. 騎師分數 (Jockey Score) - 權重 15% (取代部分 Synergy)
    j_df, j_err = fetch_hkjc_jockey_ranking()
    t_df, t_err = fetch_hkjc_trainer_ranking()
    static_df['JockeyScore'] = static_df['騎師'].apply(
        lambda x: calculate_jockey_score(str(x).strip(), j_df)
    )
    
    # 練馬師分數 (15%)
    static_df['TrainerScore'] = static_df['練馬師'].apply(
        lambda x: calculate_trainer_score(str(x).strip(), t_df)
    )
    
    # 3. 適應性分數 (Adaptability Score) - 權重 20%
    # 排位（檔位）：在該場地/距離下，外檔或內檔表現如何？
    # 假設：通常內檔 (1-4) 較好，中檔 (5-8) 次之，外檔 (9+) 較差
    
    static_df['排位_int'] = pd.to_numeric(static_df['排位'], errors='coerce').fillna(99)
    static_df['DrawScore'] = 100 - (static_df['排位_int'] - 1) * (100 / 13) # 1號檔 100分，14號檔 0分
    
    # 4. 負擔分數 (Burden Score) - 權重 10%
    # 評分與負磅的關係：評分越高負磅越重，負擔越大
    # 簡化：評分最高的馬匹，給予負擔分數較低（因為大家都看好它，但它要負重）
    static_df['Rating_int'] = pd.to_numeric(static_df['評分'], errors='coerce').fillna(0).astype(float)

    # 2. 計算最大評分
    max_rating = static_df['Rating_int'].max()
    
    # 3. 評分差異分數 (相對分數)
    # 加入條件判斷：如果最高評分大於 0 才進行除法，否則全給 0 分（或 100 分，取決於你的邏輯）
    if max_rating > 0:
        static_df['RatingDiffScore'] = (static_df['Rating_int'] / max_rating) * 100
    else:
        static_df['RatingDiffScore'] = 0.0
    
    # 4. 如果你最後一定要轉換回整數，請再次確保填補可能產生的 inf/nan
    static_df['RatingDiffScore'] = static_df['RatingDiffScore'].replace([np.inf, -np.inf], 0).fillna(0).astype(int)
    
    # --- 最終加權公式 (完全基於靜態數據) ---
    df = static_df.copy()
    
    df['TotalScore'] = (df['FormScore'] * 0.40) + \
                       (df['JockeyScore'] * 0.15) + \
                       (df['TrainerScore'] * 0.15) + \
                       (df['DrawScore'] * 0.20) + \
                       (df['RatingDiffScore'] * 0.10)
                       
    # 清理並輸出
    output_cols = ['馬名','馬齡','騎師','排位','練馬師','FormScore', 'JockeyScore', 'TrainerScore', 
                   'DrawScore', 'RatingDiffScore', 'TotalScore']
    
    # 只選取存在的欄位
    final_cols = [col for col in output_cols if col in df.columns]

    df = df[final_cols].sort_values('TotalScore', ascending=False)
    
    return df
# 嘗試加載 Race Card
date_str = str(Date)
if not st.session_state.api_called:
    with st.spinner("載入賽事資料中..."):
        if place in ["ST","HV"]:
            race_card_data = fetch_race_card(date_str, place)
        else:
            race_card_data = fetch_race_card_oversea(date_str, place,race_no)

        if race_card_data:
            st.session_state.race_dataframes = {k: v['df'] for k,v in race_card_data.items()}
            st.session_state.post_time_dict = {k: v['post_time'] for k,v in race_card_data.items()}
            st.session_state.api_called = True

# --- 顯示賽事資訊 ---
if race_no in st.session_state.race_dataframes:
    pt = st.session_state.post_time_dict.get(race_no)
    pt_str = pt.strftime("%H:%M") if pt else "--:--"
    st.info(f"📍 {place} 第 {race_no} 場 | 🕒 開跑: {pt_str}")
    with st.expander("查看排位表", expanded=False):
        st.dataframe(st.session_state.race_dataframes[race_no], width='stretch')
else:
    st.warning("找不到此場次資料，請確認日期與場地。")

# ==================== 5. 監控循環邏輯 ====================

methodlist = ['WIN', 'PLA', 'QIN', 'QPL'] # 簡化預設
time_delay = 10
if len(st.session_state.race_dataframes[race_no]['馬名'])<7:
    print_list = ['WIN&QIN','PLA']
else:
    print_list = ['WIN&QIN', 'PLA&QPL']
top_list = ['QIN']
methodCHlist = ['連贏']
for method in methodlist:
    # 確保 odds_dict, investment_dict, overall_investment_dict, diff_dict 都有 WIN/PLA/QIN/QPL 鍵
    st.session_state.odds_dict.setdefault(method, pd.DataFrame())
    st.session_state.investment_dict.setdefault(method, pd.DataFrame())
    st.session_state.overall_investment_dict.setdefault(method, pd.DataFrame())
    st.session_state.diff_dict.setdefault(method, pd.DataFrame())
    
# 確保 overall 鍵存在於整體投注量和差異字典中
st.session_state.overall_investment_dict.setdefault('overall', pd.DataFrame())
st.session_state.diff_dict.setdefault('overall', pd.DataFrame())

# ==================== 5. 監控與顯示邏輯 (使用 Fragment 避免閃爍) ====================
placeholder = st.empty()
if monitoring_on:
    while monitoring_on:
        # --- 實時監控模式 (比賽當日) ---
        #st.markdown("### 🟢 實時監控與資金流預測中...")
        time_now = datetime.now()+timedelta(hours=8)
        time_str = time_now.strftime('%H:%M:%S')
    
        # 1. 抓取數據 (這裡需要您的實際抓取邏輯)
    
    
        odds = get_odds_data()
        investments = get_investment_data()
    
        if odds and investments:
            with st.spinner(f"更新數據中 ({time_str})..."):
                # 2. 處理數據
                # 這裡需要您的 
                save_odds_data(time_now,odds)
                save_investment_data(time_now,investments,odds)
                get_overall_investment(time_now,investments)
                weird_data(investments)
                change_overall(time_now)
                # 由於篇幅限制，假設已運行
                st.session_state.last_update = time_now
        
        # 3. 顯示結果
        with placeholder.container():
            HK_TZ = timezone(timedelta(hours=8))
            now_naive = datetime.now()
            now = now_naive + datere.relativedelta(hours=8)
            now = now.replace(tzinfo=HK_TZ)
            post_time_raw = st.session_state.post_time_dict.get(race_no)
            
            if post_time_raw is None:
                        time_str = "未載入"
            else:
                        # 確保 post_time 也有時區
                        if post_time_raw.tzinfo is None:
                            post_time = post_time_raw.replace(tzinfo=HK_TZ)
                        else:
                            post_time = post_time_raw  # 已有時區
                    
                        seconds_left = (post_time - now).total_seconds()
                        
                        if seconds_left <= 0:
                            time_str = "已開跑"
                            minutes = -1
                        else:
                            minutes = int(seconds_left // 60)
                            time_str = f"離開跑 {minutes} 分"  
            last_update_str = st.session_state.last_update.strftime('%H:%M:%S') if st.session_state.last_update else "N/A"
            status_icon = "🏁" if "已開跑" in time_str else "⏳"
    
            st.markdown(f"### {status_icon} {time_str} ｜ ⏱️ 最後同步時間：`{last_update_str}`")
            
            # A. 氣泡圖 (資金流向視覺化)
            if show_bubble:
                print_bubble(race_no, print_list)
            if show_bar:    
                print_bar_chart(time_now)
            if show_move_bar:
                print_plotly_advanced_bar(race_no,print_list)
            #plot_racing_monitor_dashboard()
            # B. 實時預測排名
            st.markdown("### 🤖 實時資金流綜合預測排名")
            prediction_df = calculate_smart_score(race_no)

            if not prediction_df.empty:
                high_flow_df = prediction_df[prediction_df['MoneyFlow'] > 500]
                
                if not high_flow_df.empty:
                    new_alerts = []
                    for horse_no, row in high_flow_df.iterrows():
                        new_alerts.append({
                            "分鐘": minutes,
                            "時間": time_str,
                            "馬號": horse_no,
                            "當刻賠率": f"{row['Odds']:.1f}" if pd.notna(row['Odds']) else "-",
                            "moneyflow": round(row['MoneyFlow'], 1)
                        })
                    new_alerts_df = pd.DataFrame(new_alerts)
                    
                    # 1. 直接將新資料合併到既有的歷史紀錄中
                    combined_df = pd.concat([st.session_state.high_moneyflow_alerts, new_alerts_df], ignore_index=True)
                    
                    # 2. 核心處理：
                    #    - 先按 moneyflow 降序排序（大的在前）
                    #    - 然後針對「時間」和「馬號」這兩個欄位去重，並保留第一個（即最大的那個）
                    st.session_state.high_moneyflow_alerts = (
                        combined_df.sort_values(by='moneyflow', ascending=False)
                                   .drop_duplicates(subset=['時間','馬號'], keep='first')
                                   .reset_index(drop=True)
                    )

                # 使用 st.expander 顯示下拉式表格
                with st.expander("🚨 異常大額資金流紀錄 (MoneyFlow > 500)", expanded=False):
                    if st.session_state.high_moneyflow_alerts.empty:
                        st.info("目前尚無大於 500 的資金流紀錄。")
                    else:
                        # 將最新紀錄排在最上面以利閱讀
                        display_alerts = st.session_state.high_moneyflow_alerts.sort_values(by="分鐘", ascending=True).copy()
        
                        # 2. 在顯示之前，將 moneyflow 轉為格式化字串 (只影響顯示，不影響原始資料)
                        display_alerts["moneyflow"] = display_alerts["moneyflow"].apply(lambda x: f"{x:.1f}")
                        
                        st.table(display_alerts[["時間", "馬號", "當刻賠率", "moneyflow"]], width='stretch', hide_index=True)
                # --- 執行過濾邏輯 ---
                display_df = prediction_df.copy() 
                #current_winner = prediction_df.iloc[0]['顯示名稱']
                #st.session_state.top_rank_history.append(current_winner)
                #current_top_4 = prediction_df.head(4)['顯示名稱'].tolist()
                #st.session_state.top_4_history.extend(current_top_4)
                
                #display_df = prediction_df.copy()
                #display_df = display_df[['馬名','騎師','馬齡','Odds', 'MoneyFlow', 'TotalFormScore', 'TotalScore']]
                #display_df.columns = ['馬名','騎師','馬齡','當前賠率', '近期資金流(K)', '近績評分', '🔥綜合推薦分']
                display_df = display_df[['馬名','馬齡','騎師','排位','練馬師','Odds', 'MoneyFlow', 'OverallMoneyFlow']]
                                         #, 'TotalScore']]
                display_df.columns = ['馬名','馬齡','騎師','排位','練馬師','當前賠率', '近期資金流', '總資金流']
                                      #, '🔥綜合推薦分']
                display_df['當前賠率'] = display_df['當前賠率'].apply(lambda x: f"{x:.1f}")
                display_df['近期資金流'] = display_df['近期資金流'].apply(lambda x: f"{x:.1f}k")
                display_df['總資金流'] = display_df['總資金流'].apply(lambda x: f"{x:.1f}k")
                #display_df['🔥綜合推薦分'] = display_df['🔥綜合推薦分'].astype(int)
                                

                st.markdown("""
                    <style>
                    /* 強制所有表格的數據內容 (td) 不准換行 */
                    .stTable td {
                        white-space: nowrap !important;
                        vertical-align: middle;
                    }
                    /* 允許標題 (th) 換行，並縮小字體以騰出空間 */
                    .stTable th {
                        white-space: normal !important;
                        min-width: 60px; /* 給標題一個最小寬度，迫使它太擠時自動換行 */
                        font-size: 14px !important;
                        line-height: 1.1;
                    }
                    </style>
                    """, unsafe_allow_html=True)
                 
                st.table(display_df.style.hide(axis='index'))   

                # 應用高亮函數
                #st.table(display_df.style.apply(highlight_top_realtime, axis=1).hide(axis='index'))                
                #if len(st.session_state.top_rank_history) > 20:
                    #st.session_state.top_rank_history.pop(0)
                #if len(st.session_state.top_4_history) > 80:
                    #st.session_state.top_4_history = st.session_state.top_4_history[4:]

                #st.markdown("### 🏆 第一名佔有率")
                #counts_1 = Counter(st.session_state.top_rank_history)
                #df_1 = pd.DataFrame({'馬名': list(counts_1.keys()), '次數': list(counts_1.values())})
                #fig1 = px.pie(df_1, values='次數', names='馬名', hole=0.4, color_discrete_sequence=px.colors.qualitative.Set3)
                #fig1.update_traces(
                        #textposition='auto',  # 自動判斷放裡面或外面
                        #textinfo='label+percent',
                        #insidetextorientation='horizontal' # 確保裡面的文字是水平的，比較好讀)
                #st.plotly_chart(fig1, width='stretch', key=f"top1_{time_now.strftime('%H%M%S')}")

                #col1, col2 = st.columns(2) # 使用左右兩欄顯示兩個圖
                
                    
            
                #with col2:
                    #st.markdown("### 🐎 頭 4 名出現頻率")
                    #counts_4 = Counter(st.session_state.top_4_history)
                    #df_4 = pd.DataFrame({'馬名': list(counts_4.keys()), '出現次數': list(counts_4.values())})
                    # 排序讓圖表更好看
                    #df_4 = df_4.sort_values(by='出現次數', ascending=False)
                    #fig4 = px.pie(df_4, values='出現次數', names='馬名', hole=0.4, color_discrete_sequence=px.colors.qualitative.Pastel)
                    #fig4.update_traces(
                        #textposition='auto',
                        #textinfo='label+percent',
                        #insidetextorientation='horizontal'
                    #)
                    #st.plotly_chart(fig4, width='stretch', key=f"top4_{time_now.strftime('&H%M%S')}")
            if show_top:
                st.markdown("### 連贏賠率排名")
                print_top()
                
            if show_henery:
                print_henery_model(gamma=1.18)
            time.sleep(time_delay)
        


else:
    # 4. 賽前預測模式 (靜態)
    st.markdown("### 🔍 賽前靜態預測分析")
    st.info("由於缺乏實時賠率和資金流數據，本分析完全基於馬匹、騎師和場地等靜態資訊。")

    # 執行靜態預測
    static_prediction_df = calculate_smart_score_static(race_no)
    if not static_prediction_df.empty:
        # 整理顯示格式
        display_df = static_prediction_df.copy()
        display_df = display_df[['馬名','馬齡','騎師','排位','練馬師', 'FormScore', 'JockeyScore', 'TrainerScore', 
                   'DrawScore', 'RatingDiffScore', 'TotalScore']]
        display_df.columns = ['馬名','馬齡','騎師','排位','練馬師','近績狀態分','騎師分','練馬師分', '檔位優勢分', '評分負擔分', '🏆 靜態預測分']

        # 格式化
        display_df['近績狀態分'] = display_df['近績狀態分'].astype(int)
        display_df['騎師分'] = display_df['騎師分'].astype(int)
        display_df['練馬師分'] = display_df['練馬師分'].astype(int)
        display_df['檔位優勢分'] = display_df['檔位優勢分'].astype(int)
        display_df['評分負擔分'] = display_df['評分負擔分'].astype(int)
        display_df['🏆 靜態預測分'] = display_df['🏆 靜態預測分'].apply(lambda x: f"{x:.1f}")


        st.dataframe(display_df, width='stretch')
