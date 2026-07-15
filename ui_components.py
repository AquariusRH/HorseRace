# ui_components.py
import sys
import traceback
import pandas as pd
import numpy as np
from PySide6 import QtCore, QtWidgets, QtGui
from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                               QTabWidget, QTextEdit, QDateEdit, QSpinBox, QComboBox)
# QWebEngineView is optional; try import and provide fallback.
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    HAS_WEBENGINE = True
except Exception:
    QWebEngineView = None
    HAS_WEBENGINE = False

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
import matplotlib.pyplot as plt
import plotly.graph_objects as go
import webbrowser
from datetime import date, datetime

from core_logic import DataEngineWorker  # ensure core_logic.py is in same directory

# -------------------------
# Helper: safe styler -> html
# -------------------------
def styler_to_html(styler):
    """
    Convert a pandas Styler to HTML in a pandas-version-safe way.
    Attempts styler.hide(axis='index') first (pandas 2.0+), falls back to hide_index().
    """
    try:
        if hasattr(styler, "hide"):
            try:
                styler = styler.hide(axis="index")
            except Exception:
                # best-effort fallback
                if hasattr(styler, "hide_index"):
                    try:
                        styler = styler.hide_index()
                    except Exception:
                        pass
        else:
            if hasattr(styler, "hide_index"):
                try:
                    styler = styler.hide_index()
                except Exception:
                    pass
    except Exception:
        # If anything goes wrong, ignore and continue to to_html()
        pass
    try:
        return styler.to_html()
    except Exception:
        # final fallback: plain HTML representation
        try:
            df = styler.data if hasattr(styler, "data") else None
            if isinstance(df, pd.DataFrame):
                return df.to_html()
        except Exception:
            pass
        return "<html><body><pre>Failed to render table</pre></body></html>"

# -------------------------
# RealTimeTab: the main UI
# -------------------------
class RealTimeTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.latest_data = {}
        self.worker = None

        self._build_ui()
        self._connect_slots()

    def _build_ui(self):
        root = QVBoxLayout(self)

        # Controls row
        controls = QHBoxLayout()
        self.date_edit = QDateEdit(QtCore.QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        controls.addWidget(QLabel("Race Date:"))
        controls.addWidget(self.date_edit)

        self.place_box = QComboBox()
        self.place_box.addItems(["ST", "HV", "HV2", "ST2"])  # example; user can edit
        controls.addWidget(QLabel("Venue:"))
        controls.addWidget(self.place_box)

        self.race_spin = QSpinBox()
        self.race_spin.setMinimum(1)
        self.race_spin.setMaximum(20)
        controls.addWidget(QLabel("Race #:"))
        controls.addWidget(self.race_spin)

        self.method_box = QComboBox()
        self.method_box.addItems(["WIN", "PLA", "QIN", "QPL"])  # user can change to multiselect in future
        controls.addWidget(QLabel("Method:"))
        controls.addWidget(self.method_box)

        self.delay_spin = QSpinBox()
        self.delay_spin.setMinimum(1)
        self.delay_spin.setMaximum(600)
        self.delay_spin.setValue(5)
        controls.addWidget(QLabel("Delay (s):"))
        controls.addWidget(self.delay_spin)

        self.start_btn = QPushButton("Start")
        self.stop_btn = QPushButton("Stop")
        self.reset_btn = QPushButton("Reset Data")
        controls.addWidget(self.start_btn)
        controls.addWidget(self.stop_btn)
        controls.addWidget(self.reset_btn)

        root.addLayout(controls)

        # Tabs
        self.tabs = QTabWidget()
        # Results tab per pool
        self.win_tab = QWidget()
        self.pla_tab = QWidget()
        self.henery_tab = QWidget()
        self.charts_tab = QWidget()
        self.rank_tab = QWidget()

        self.tabs.addTab(self.win_tab, "WIN")
        self.tabs.addTab(self.pla_tab, "PLA")
        self.tabs.addTab(self.henery_tab, "Henery")
        self.tabs.addTab(self.charts_tab, "Charts")
        self.tabs.addTab(self.rank_tab, "Rankings")

        root.addWidget(self.tabs, stretch=1)

        # Logging area
        log_layout = QVBoxLayout()
        log_layout.addWidget(QLabel("Log:"))
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        log_layout.addWidget(self.log)
        root.addLayout(log_layout)

        # Build inner tab contents
        self._build_win_tab()
        self._build_pla_tab()
        self._build_henery_tab()
        self._build_charts_tab()
        self._build_rank_tab()

        self.setLayout(root)

    def _build_win_tab(self):
        layout = QVBoxLayout()
        self.win_web = QWebEngineView() if HAS_WEBENGINE else None
        if self.win_web:
            layout.addWidget(self.win_web)
        else:
            self.win_fallback = QTextEdit()
            self.win_fallback.setReadOnly(True)
            layout.addWidget(self.win_fallback)
        self.win_tab.setLayout(layout)

    def _build_pla_tab(self):
        layout = QVBoxLayout()
        self.pla_web = QWebEngineView() if HAS_WEBENGINE else None
        if self.pla_web:
            layout.addWidget(self.pla_web)
        else:
            self.pla_fallback = QTextEdit()
            self.pla_fallback.setReadOnly(True)
            layout.addWidget(self.pla_fallback)
        self.pla_tab.setLayout(layout)

    def _build_henery_tab(self):
        layout = QVBoxLayout()
        self.henery_web = QWebEngineView() if HAS_WEBENGINE else None
        if self.henery_web:
            layout.addWidget(self.henery_web)
        else:
            self.henery_fallback = QTextEdit()
            self.henery_fallback.setReadOnly(True)
            layout.addWidget(self.henery_fallback)
        self.henery_tab.setLayout(layout)

    def _build_charts_tab(self):
        layout = QVBoxLayout()
        # Matplotlib canvas
        self.fig, self.ax = plt.subplots(figsize=(8, 4))
        self.canvas = FigureCanvas(self.fig)
        layout.addWidget(self.canvas)

        # Plotly webviews
        self.plotly_bubble = QWebEngineView() if HAS_WEBENGINE else None
        self.plotly_advanced = QWebEngineView() if HAS_WEBENGINE else None
        if self.plotly_bubble:
            layout.addWidget(self.plotly_bubble, stretch=1)
        else:
            self.plotly_bubble_fallback = QTextEdit()
            self.plotly_bubble_fallback.setReadOnly(True)
            layout.addWidget(self.plotly_bubble_fallback)

        if self.plotly_advanced:
            layout.addWidget(self.plotly_advanced, stretch=1)
        else:
            self.plotly_advanced_fallback = QTextEdit()
            self.plotly_advanced_fallback.setReadOnly(True)
            layout.addWidget(self.plotly_advanced_fallback)
        self.charts_tab.setLayout(layout)

    def _build_rank_tab(self):
        layout = QVBoxLayout()
        self.rank_web = QWebEngineView() if HAS_WEBENGINE else None
        if self.rank_web:
            layout.addWidget(self.rank_web)
        else:
            self.rank_fallback = QTextEdit()
            self.rank_fallback.setReadOnly(True)
            layout.addWidget(self.rank_fallback)
        self.rank_tab.setLayout(layout)

    def _connect_slots(self):
        self.start_btn.clicked.connect(self.start_worker)
        self.stop_btn.clicked.connect(self.stop_worker)
        self.reset_btn.clicked.connect(self._on_reset_clicked)

    @Slot()
    def start_worker(self):
        if self.worker and self.worker.isRunning():
            self._append_log("Worker already running.")
            return
        race_date = self.date_edit.date().toString("yyyy-MM-dd")
        place = self.place_box.currentText()
        race_no = self.race_spin.value()
        method = self.method_box.currentText()
        methods = [method]  # as list (parity with streamlit methodlist)
        delay = self.delay_spin.value()

        self._append_log(f"Starting worker: {race_date} R{race_no} @ {place}, methods={methods}, delay={delay}s")

        # Create and start worker
        self.worker = DataEngineWorker(race_date, place, race_no, methods, time_delay=delay)
        self.worker.data_ready_signal.connect(self.handle_new_data)
        self.worker.log_signal.connect(self._append_log)
        # ensure stop handler exists
        self.worker.stopped_signal.connect(self._on_worker_stopped)

        self.worker.start()

    @Slot()
    def stop_worker(self):
        if self.worker:
            self._append_log("Stopping worker...")
            self.worker.stop()
        else:
            self._append_log("No active worker to stop.")

    @Slot()
    def _on_worker_stopped(self):
        self._append_log("Worker has stopped.")
        # Keep UI state; do not clear display unless reset clicked.

    @Slot()
    def _on_reset_clicked(self):
        self._append_log("Reset button clicked: clearing worker and UI state.")
        # Reset core worker state
        if self.worker:
            try:
                self.worker.reset_state()
            except Exception as e:
                self._append_log(f"Reset worker state error: {e}")
        # Clear latest_data and displays
        self.latest_data = {}
        # Clear web views / fallbacks
        try:
            if HAS_WEBENGINE:
                for w in (self.win_web, self.pla_web, self.henery_web, self.plotly_bubble, self.plotly_advanced, self.rank_web):
                    if w:
                        w.setHtml("<html><body></body></html>")
            else:
                for t in (getattr(self, 'win_fallback', None), getattr(self, 'pla_fallback', None),
                          getattr(self, 'henery_fallback', None), getattr(self, 'plotly_bubble_fallback', None),
                          getattr(self, 'plotly_advanced_fallback', None), getattr(self, 'rank_fallback', None)):
                    if t:
                        t.clear()
            # Clear matplotlib
            self.ax.clear()
            self.canvas.draw()
        except Exception as e:
            self._append_log(f"UI clear error: {e}")

    @Slot(str)
    def _append_log(self, text):
        # QTextEdit.append is available and appends with newline
        try:
            self.log.append(text)
        except Exception:
            try:
                self.log.insertPlainText(text + "\n")
            except Exception:
                print(text)

    @Slot(dict)
    def handle_new_data(self, data):
        """
        Main-thread-only rendering of tables and charts.
        Expects data keys like 'WIN_odds', 'WIN_investment', 'henery', 'jockey_rank', 'trainer_rank', 'timestamp'
        Must use explicit None checks for DataFrames to avoid pandas truth-value ambiguity.
        """
        try:
            self.latest_data = data.copy()

            timestamp = data.get("timestamp", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
            self._append_log(f"handle_new_data: received at {timestamp}")

            # Safe retrieval with explicit None checks
            win_odds_raw = data.get("WIN_odds")
            win_odds = win_odds_raw if win_odds_raw is not None else pd.DataFrame()

            win_invest_raw = data.get("WIN_investment")
            win_invest = win_invest_raw if win_invest_raw is not None else pd.DataFrame()

            pla_odds_raw = data.get("PLA_odds")
            pla_odds = pla_odds_raw if pla_odds_raw is not None else pd.DataFrame()

            pla_invest_raw = data.get("PLA_investment")
            pla_invest = pla_invest_raw if pla_invest_raw is not None else pd.DataFrame()

            henery_df_raw = data.get("henery")
            henery_df = henery_df_raw if henery_df_raw is not None else pd.DataFrame()

            jockey_raw = data.get("jockey_rank")
            jockey_rank = jockey_raw if jockey_raw is not None else pd.DataFrame()

            trainer_raw = data.get("trainer_rank")
            trainer_rank = trainer_raw if trainer_raw is not None else pd.DataFrame()

            # -----------------------
            # Render WIN table (styled HTML)
            # -----------------------
            if not win_invest.empty:
                latest_inv = win_invest.tail(1)
                latest_odds = win_odds.tail(1) if not win_odds.empty else pd.DataFrame()
                # align index/columns
                try:
                    display_df = pd.DataFrame({
                        "odds": latest_odds.iloc[0].values if not latest_odds.empty else [np.nan]*len(latest_inv.columns),
                        "investment": latest_inv.iloc[0].values
                    }, index=latest_inv.columns)
                    display_df.index.name = "Horse"
                    # Add rank-change badge column based on diff if available (diff exists in payload as WIN_diff)
                    win_diff_raw = data.get("WIN_diff")
                    win_diff = win_diff_raw if win_diff_raw is not None else pd.DataFrame()
                    if not win_diff.empty:
                        last_diff = win_diff.tail(1).iloc[0]
                        # Create arrow badges
                        def arrow_badge(v):
                            try:
                                v = float(v)
                                if v > 0:
                                    return '<span style="color:green">▲{}</span>'.format(int(v))
                                elif v < 0:
                                    return '<span style="color:red">▼{}</span>'.format(abs(int(v)))
                                else:
                                    return '-'
                            except Exception:
                                return '-'
                        display_df['change'] = [arrow_badge(last_diff.get(col, 0)) for col in display_df.index]
                    # style
                    sty = display_df.style.format({"odds": "{:.2f}", "investment": "{:.2f}"}).set_table_attributes('border="0" class="dataframe table table-striped"')
                    html = styler_to_html(sty)
                except Exception as e:
                    self._append_log(f"WIN table formatting error: {e}")
                    html = "<pre>WIN table render error</pre>"
            else:
                html = "<html><body><i>No WIN investment data yet.</i></body></html>"

            if HAS_WEBENGINE and getattr(self, "win_web", None):
                try:
                    self.win_web.setHtml(html)
                except Exception as e:
                    self._append_log(f"WIN webengine setHtml error: {e}")
                    if getattr(self, "win_fallback", None):
                        self.win_fallback.setPlainText(html)
            else:
                if getattr(self, "win_fallback", None):
                    self.win_fallback.setPlainText(html)

            # -----------------------
            # Render PLA table
            # -----------------------
            if not pla_invest.empty:
                latest_inv = pla_invest.tail(1)
                latest_odds = pla_odds.tail(1) if not pla_odds.empty else pd.DataFrame()
                try:
                    display_df = pd.DataFrame({
                        "odds": latest_odds.iloc[0].values if not latest_odds.empty else [np.nan]*len(latest_inv.columns),
                        "investment": latest_inv.iloc[0].values
                    }, index=latest_inv.columns)
                    display_df.index.name = "Horse"
                    # diff badges
                    pla_diff_raw = data.get("PLA_diff")
                    pla_diff = pla_diff_raw if pla_diff_raw is not None else pd.DataFrame()
                    if not pla_diff.empty:
                        last_diff = pla_diff.tail(1).iloc[0]
                        def arrow_badge(v):
                            try:
                                v = float(v)
                                if v > 0:
                                    return '<span style="color:green">▲{}</span>'.format(int(v))
                                elif v < 0:
                                    return '<span style="color:red">▼{}</span>'.format(abs(int(v)))
                                else:
                                    return '-'
                            except Exception:
                                return '-'
                        display_df['change'] = [arrow_badge(last_diff.get(col, 0)) for col in display_df.index]
                    sty = display_df.style.format({"odds": "{:.2f}", "investment": "{:.2f}"}).set_table_attributes('border="0" class="dataframe table table-striped"')
                    html_pla = styler_to_html(sty)
                except Exception as e:
                    self._append_log(f"PLA table formatting error: {e}")
                    html_pla = "<pre>PLA table render error</pre>"
            else:
                html_pla = "<html><body><i>No PLA investment data yet.</i></body></html>"

            if HAS_WEBENGINE and getattr(self, "pla_web", None):
                try:
                    self.pla_web.setHtml(html_pla)
                except Exception as e:
                    self._append_log(f"PLA webengine setHtml error: {e}")
                    if getattr(self, "pla_fallback", None):
                        self.pla_fallback.setPlainText(html_pla)
            else:
                if getattr(self, "pla_fallback", None):
                    self.pla_fallback.setPlainText(html_pla)

            # -----------------------
            # Render Henery table
            # -----------------------
            try:
                if isinstance(henery_df, pd.DataFrame) and not henery_df.empty:
                    sty = henery_df.style.format({
                        "odds": "{:.2f}",
                        "implied_prob": "{:.2f}%",
                        "theoretical_prob": "{:.2f}%",
                        "value_index": "{:.3f}",
                        "expected_edge_pct": "{:.2f}%",
                        "suggested_fraction": "{:.4f}"
                    }).set_table_attributes('class="dataframe table table-striped"')
                    html_h = styler_to_html(sty)
                else:
                    html_h = "<html><body><i>No Henery predictions yet.</i></body></html>"
            except Exception as e:
                self._append_log(f"Henery formatting error: {e}")
                html_h = "<pre>Henery render error</pre>"

            if HAS_WEBENGINE and getattr(self, "henery_web", None):
                try:
                    self.henery_web.setHtml(html_h)
                except Exception as e:
                    self._append_log(f"Henery webengine setHtml error: {e}")
                    if getattr(self, "henery_fallback", None):
                        self.henery_fallback.setPlainText(html_h)
            else:
                if getattr(self, "henery_fallback", None):
                    self.henery_fallback.setPlainText(html_h)

            # -----------------------
            # Render Rankings table
            # -----------------------
            try:
                if not jockey_rank.empty:
                    sty = jockey_rank.head(50).style.set_table_attributes('class="dataframe table table-sm"')
                    html_r = styler_to_html(sty)
                elif not trainer_rank.empty:
                    sty = trainer_rank.head(50).style.set_table_attributes('class="dataframe table table-sm"')
                    html_r = styler_to_html(sty)
                else:
                    html_r = "<html><body><i>No ranking data yet.</i></body></html>"
            except Exception as e:
                self._append_log(f"Rankings formatting error: {e}")
                html_r = "<pre>Rankings render error</pre>"

            if HAS_WEBENGINE and getattr(self, "rank_web", None):
                try:
                    self.rank_web.setHtml(html_r)
                except Exception as e:
                    self._append_log(f"Rank webengine setHtml error: {e}")
                    if getattr(self, "rank_fallback", None):
                        self.rank_fallback.setPlainText(html_r)
            else:
                if getattr(self, "rank_fallback", None):
                    self.rank_fallback.setPlainText(html_r)

            # -----------------------
            # Matplotlib real-time chart (print_bar_chart-like)
            # -----------------------
            try:
                self.ax.clear()
                # Use overall_investment time series if present
                overall_df = data.get("overall_investment")
                if isinstance(overall_df, pd.DataFrame) and not overall_df.empty:
                    # plot last 5 timestamps stacked bars per horse
                    df = overall_df.tail(5).copy()
                    df.index = pd.to_datetime(df.index)
                    df.plot(kind='bar', ax=self.ax)
                    self.ax.set_title(f"Overall investment (last {len(df)} samples) - {timestamp}")
                    self.ax.set_xlabel("Timestamp")
                    self.ax.set_ylabel("Investment")
                else:
                    self.ax.text(0.5, 0.5, "No overall investment history yet", ha='center')
                self.canvas.draw()
            except Exception as e:
                self._append_log(f"Matplotlib draw error: {e}\n{traceback.format_exc()}")

            # -----------------------
            # Plotly bubble & advanced bar charts
            # -----------------------
            try:
                # Bubble: use latest investments vs odds
                if not win_invest.empty and not win_odds.empty:
                    latest_inv = win_invest.tail(1).iloc[0]
                    latest_od = win_odds.tail(1).iloc[0]
                    horses = list(latest_inv.index.astype(str))
                    x = latest_od.values
                    y = latest_inv.values
                    sizes = np.clip(np.array(y, dtype=float), 1, None) * 3
                    fig = go.Figure(data=[go.Scatter(
                        x=x, y=y, text=horses, mode='markers', marker=dict(size=sizes, color=x, colorscale='Viridis', showscale=True)
                    )])
                    fig.update_layout(title="Bubble: Odds vs Investment", xaxis_title="Odds", yaxis_title="Investment")
                    html_bubble = fig.to_html(include_plotlyjs='cdn', full_html=False)
                else:
                    html_bubble = "<html><body><i>No bubble data yet.</i></body></html>"
            except Exception as e:
                self._append_log(f"Plotly bubble build error: {e}")
                html_bubble = "<html><body><i>Bubble build error</i></body></html>"

            if HAS_WEBENGINE and getattr(self, "plotly_bubble", None):
                try:
                    self.plotly_bubble.setHtml(html_bubble)
                except Exception as e:
                    self._append_log(f"plotly_bubble setHtml error: {e}")
                    if getattr(self, "plotly_bubble_fallback", None):
                        self.plotly_bubble_fallback.setPlainText(html_bubble)
            else:
                if getattr(self, "plotly_bubble_fallback", None):
                    self.plotly_bubble_fallback.setPlainText(html_bubble)

            # Advanced bar: value_index from henery_df
            try:
                if isinstance(henery_df, pd.DataFrame) and not henery_df.empty:
                    fig2 = go.Figure()
                    fig2.add_trace(go.Bar(x=henery_df.index.astype(str), y=henery_df['value_index'], name='Value Index'))
                    fig2.update_layout(title="Henery Value Index", xaxis_title="Horse", yaxis_title="Value Index")
                    html_adv = fig2.to_html(include_plotlyjs='cdn', full_html=False)
                else:
                    html_adv = "<html><body><i>No advanced data yet.</i></body></html>"
            except Exception as e:
                self._append_log(f"Plotly advanced build error: {e}")
                html_adv = "<html><body><i>Advanced build error</i></body></html>"

            if HAS_WEBENGINE and getattr(self, "plotly_advanced", None):
                try:
                    self.plotly_advanced.setHtml(html_adv)
                except Exception as e:
                    self._append_log(f"plotly_advanced setHtml error: {e}")
                    if getattr(self, "plotly_advanced_fallback", None):
                        self.plotly_advanced_fallback.setPlainText(html_adv)
            else:
                if getattr(self, "plotly_advanced_fallback", None):
                    self.plotly_advanced_fallback.setPlainText(html_adv)

        except Exception as e:
            self._append_log(f"handle_new_data unexpected error: {e}\n{traceback.format_exc()}")

# Optionally provide a main for quick local testing
if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    w = RealTimeTab()
    w.resize(1200, 800)
    w.show()
    sys.exit(app.exec())