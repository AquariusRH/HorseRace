"""
ui_components.py

PySide6 UI components implementing a Real-time Scraper tab and core interactions.
Contains a TabWidget with tabs for:
- Real-time Scraper
- Rankings (Jockey / Trainer)
- Processing / Scoring
- Visualizations (Matplotlib & Plotly embedding)
- Settings / Export

Defines a ScrapeWorker (QRunnable) that runs scraping in background and emits signals
for status updates and results.
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextEdit,
    QTabWidget, QFileDialog, QMessageBox, QComboBox, QSpinBox, QProgressBar, QTableView, QDateEdit
)
from PySide6.QtCore import Qt, Slot, QThreadPool, QObject, Signal, QRunnable, QThread, QDate
from PySide6.QtWebEngineWidgets import QWebEngineView
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
try:
    import plotly.io as pio
except Exception:
    pio = None
import tempfile
import os
import core_logic
import pandas as pd
import numpy as np
import time
from datetime import datetime

from PySide6.QtCore import QAbstractTableModel, QModelIndex
class PandasModel(QAbstractTableModel):
    def __init__(self, df=pd.DataFrame(), parent=None):
        super().__init__(parent)
        self._df = df
        self._highlight = None  # DataFrame or dict for highlighting

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            try:
                return str(self._df.columns[section])
            except Exception:
                return ""
        else:
            try:
                return str(self._df.index[section])
            except Exception:
                return ""

    def rowCount(self, parent=QModelIndex()):
        return len(self._df.index)

    def columnCount(self, parent=QModelIndex()):
        return len(self._df.columns)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r = index.row(); c = index.column()
        if role == Qt.DisplayRole:
            val = self._df.iloc[r, c]
            return str(val)
        if role == Qt.BackgroundRole and self._highlight is not None:
            try:
                col = self._df.columns[c]
                # highlight can be dict or DataFrame/Series
                val = None
                if isinstance(self._highlight, dict):
                    val = self._highlight.get(col)
                else:
                    # assume Series-like with col as key
                    if col in self._highlight:
                        val = self._highlight[col]
                if val is None:
                    return None
                try:
                    v = float(val)
                    if v > 0:
                        from PySide6.QtGui import QBrush, QColor
                        return QBrush(QColor(200, 255, 200))
                    elif v < 0:
                        from PySide6.QtGui import QBrush, QColor
                        return QBrush(QColor(255, 200, 200))
                except Exception:
                    return None
            except Exception:
                return None
        return None

    def setDataFrame(self, df: pd.DataFrame):
        self.beginResetModel()
        self._df = df
        self.endResetModel()

    def setHighlight(self, highlight):
        """Highlight can be a dict mapping column -> numeric change, or a Series/DataFrame."""
        self._highlight = highlight
        # trigger repaint
        self.dataChanged.emit(self.index(0,0), self.index(max(0,self.rowCount()-1), max(0,self.columnCount()-1)))


class MatplotlibCanvas(FigureCanvas):
    def __init__(self, parent=None, width=6, height=4, dpi=100):
        fig = Figure(figsize=(width, height), dpi=dpi)
        self.axes = fig.add_subplot(111)
        super().__init__(fig)
        self.setParent(parent)

    def display_figure(self, fig):
        # Replace internal figure with provided fig
        self.figure = fig
        self.draw()


# Worker signals container
class WorkerSignals(QObject):
    status = Signal(str)
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class ScrapeWorker(QRunnable):
    """Runs core_logic.scrape_realtime in a background thread and emits signals."""
    def __init__(self, date: str, place: str, race_no: int, methods: list):
        super().__init__()
        self.date = date
        self.place = place
        self.race_no = race_no
        self.methods = methods
        self.signals = WorkerSignals()

    def run(self):
        try:
            def status_cb(msg):
                self.signals.status.emit(msg)
            result = core_logic.scrape_realtime(self.date, self.place, self.race_no, self.methods, status_callback=status_cb)
            self.signals.result.emit(result)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()


class LiveUpdateWorker(QThread):
    """Background thread that continuously fetches data and emits updates."""
    update = Signal(object)
    status = Signal(str)
    error = Signal(str)

    def __init__(self, date: str, place: str, race_no: int, methods: list, time_delay: float = 5.0):
        super().__init__()
        self.date = date
        self.place = place
        self.race_no = race_no
        self.methods = methods
        self.time_delay = float(time_delay)
        self._running = False

    def stop(self):
        self._running = False

    def run(self):
        self._running = True
        self.status.emit('Live update started')
        try:
            while self._running:
                self.status.emit('Fetching odds...')
                try:
                    odds = core_logic.get_odds_data(self.date, self.place, self.race_no, self.methods)
                    self.status.emit('Fetching investments...')
                    investments = core_logic.get_investment_data(self.date, self.place, self.race_no, self.methods)
                    now_ts = datetime.utcnow()
                    core_logic.save_odds_data(now_ts, odds, self.methods)
                    core_logic.save_investment_data(now_ts, investments, odds, self.methods)
                    diffs = core_logic.weird_data_calc(investments, odds, self.methods)
                    result = {'odds': odds, 'investments': investments, 'diffs': diffs}
                    self.update.emit(result)
                    self.status.emit('Update emitted')
                except Exception as e:
                    self.error.emit(str(e))
                # sleep but check running flag periodically
                slept = 0.0
                interval = 0.2
                while self._running and slept < self.time_delay:
                    time.sleep(interval)
                    slept += interval
        finally:
            self.status.emit('Live update stopped')


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Jockey Race - Desktop')
        self.resize(1200, 800)
        self.threadpool = QThreadPool.globalInstance()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._make_scraper_tab(), 'Real-time Scraper')
        tabs.addTab(self._make_rank_tab(), 'Rankings')
        tabs.addTab(self._make_process_tab(), 'Process & Score')
        tabs.addTab(self._make_visual_tab(), 'Visualization')
        tabs.addTab(self._make_settings_tab(), 'Settings')
        layout.addWidget(tabs)
        self.setLayout(layout)

    def _make_scraper_tab(self):
        w = QWidget()
        layout = QVBoxLayout()

        control_h = QHBoxLayout()
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat('yyyy-MM-dd')
        # default to today
        self.date_edit.setDate(QDate.currentDate())
        self.place_combo = QComboBox()
        self.place_combo.addItems(['ST', 'HV', 'ST-HK', 'HV-HK'])
        self.race_spin = QSpinBox(); self.race_spin.setMinimum(1); self.race_spin.setMaximum(20)
        self.methods_input = QLineEdit(); self.methods_input.setPlaceholderText('WIN,PLA,QIN')
        self.scrape_btn = QPushButton('Start Scrape')
        self.scrape_btn.clicked.connect(self.on_start_scrape)
        control_h.addWidget(QLabel('Date:'))
        control_h.addWidget(self.date_edit)
        control_h.addWidget(QLabel('Place:'))
        control_h.addWidget(self.place_combo)
        control_h.addWidget(QLabel('Race No:'))
        control_h.addWidget(self.race_spin)
        control_h.addWidget(QLabel('Methods:'))
        control_h.addWidget(self.methods_input)
        control_h.addWidget(self.scrape_btn)

        layout.addLayout(control_h)

        # Status & progress
        self.status_log = QTextEdit(); self.status_log.setReadOnly(True); self.status_log.setFixedHeight(150)
        layout.addWidget(self.status_log)
        self.progress = QProgressBar(); self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # Auto-update controls
        auto_h = QHBoxLayout()
        self.start_auto_btn = QPushButton('Start Real-Time Auto-Update')
        self.stop_auto_btn = QPushButton('Stop')
        self.stop_auto_btn.setEnabled(False)
        auto_h.addWidget(self.start_auto_btn)
        auto_h.addWidget(self.stop_auto_btn)
        layout.addLayout(auto_h)

        # Per-pool tabs for results
        self.METHODS = ['WIN','PLA','QIN','QPL','FCT','TRI','FF']
        self.pool_tabs = QTabWidget()
        self.pool_tables = {}
        self.pool_models = {}
        for m in self.METHODS:
            tbl = QTableView()
            model = PandasModel(pd.DataFrame())
            tbl.setModel(model)
            self.pool_tables[m] = tbl
            self.pool_models[m] = model
            self.pool_tabs.addTab(tbl, m)
        layout.addWidget(self.pool_tabs, stretch=1)

        w.setLayout(layout)

        # Connect auto buttons
        self.start_auto_btn.clicked.connect(self.on_start_auto)
        self.stop_auto_btn.clicked.connect(self.on_stop_auto)
        return w

    def _make_rank_tab(self):
        w = QWidget(); layout = QVBoxLayout()
        hl = QHBoxLayout()
        self.jockey_btn = QPushButton('Fetch Jockey Ranking')
        self.jockey_btn.clicked.connect(self.on_fetch_jockey)
        self.trainer_btn = QPushButton('Fetch Trainer Ranking')
        self.trainer_btn.clicked.connect(self.on_fetch_trainer)
        hl.addWidget(self.jockey_btn); hl.addWidget(self.trainer_btn)
        layout.addLayout(hl)
        self.rank_output = QTextEdit(); self.rank_output.setReadOnly(True)
        layout.addWidget(self.rank_output)
        w.setLayout(layout)
        return w

    def _make_process_tab(self):
        w = QWidget(); layout = QVBoxLayout()
        self.process_btn = QPushButton('Process & Score Latest Data')
        self.process_btn.clicked.connect(self.on_process)
        layout.addWidget(self.process_btn)
        self.process_output = QTextEdit(); self.process_output.setReadOnly(True)
        layout.addWidget(self.process_output)
        w.setLayout(layout)
        return w

    def _make_visual_tab(self):
        w = QWidget(); layout = QVBoxLayout()
        self.plot_canvas = MatplotlibCanvas(self, width=8, height=5)
        toolbar = NavigationToolbar(self.plot_canvas, self)
        layout.addWidget(toolbar)
        layout.addWidget(self.plot_canvas)
        # Plotly view
        self.plotly_view = QWebEngineView()
        layout.addWidget(self.plotly_view, stretch=1)
        btn_h = QHBoxLayout()
        self.plot_bar_btn = QPushButton('Plot Sample Bar')
        self.plot_bar_btn.clicked.connect(self.on_plot_bar)
        self.plot_bubble_btn = QPushButton('Plot Sample Bubble (Plotly)')
        self.plot_bubble_btn.clicked.connect(self.on_plot_bubble)
        btn_h.addWidget(self.plot_bar_btn); btn_h.addWidget(self.plot_bubble_btn)
        layout.addLayout(btn_h)
        w.setLayout(layout)
        return w

    def _make_settings_tab(self):
        w = QWidget(); layout = QVBoxLayout()
        self.export_btn = QPushButton('Export Latest Results')
        self.export_btn.clicked.connect(self.on_export)
        layout.addWidget(self.export_btn)
        w.setLayout(layout)
        return w

    # ---------------- Slots / Callbacks ----------------
    @Slot()
    def on_start_scrape(self):
        date = self.date_edit.date().toString('yyyy-MM-dd')
        place = self.place_combo.currentText()
        race_no = int(self.race_spin.value())
        methods = [m.strip() for m in (self.methods_input.text() or 'WIN,PLA').split(',') if m.strip()]
        if not date:
            QMessageBox.warning(self, 'Input required', 'Please input a date (YYYY-MM-DD)')
            return
        self.status_log.clear()
        self.append_status('Scheduling single scrape...')
        self.scrape_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0,0)  # Indeterminate

        worker = ScrapeWorker(date, place, race_no, methods)
        worker.signals.status.connect(self.append_status)
        worker.signals.result.connect(self.on_scrape_result)
        worker.signals.error.connect(self.on_scrape_error)
        worker.signals.finished.connect(self.on_scrape_finished)
        self.threadpool.start(worker)

    @Slot(str)
    def append_status(self, msg: str):
        from datetime import datetime as _dt
        timestamp = _dt.now().strftime('%H:%M:%S')
        self.status_log.append(f"[{timestamp}] {msg}")

    @Slot(object)
    def on_scrape_result(self, result):
        # result is dict with keys: odds, investments, age, display_df
        self.append_status('Scrape result received. Updating UI...')
        odds = result.get('odds', {})
        investments = result.get('investments', {})
        # Update each pool tab separately
        for m in self.METHODS:
            try:
                if m in ['WIN','PLA']:
                    vals = odds.get(m, [])
                    inv = core_logic.core_state.investment_dict.get(m, pd.DataFrame())
                    if vals:
                        cols = [str(i+1) for i in range(len(vals))]
                        odds_row = [float(x) if x != np.inf else np.nan for x in vals]
                        # get latest investment row from core_state if exists
                        invest_row = None
                        if not inv.empty:
                            invest_row = inv.iloc[-1].tolist()
                        else:
                            # compute per-horse using pool if available
                            pool = investments.get(m, [None])[0] if investments.get(m) else None
                            if pool is not None:
                                invest_row = [round(pool / 1000 / float(o) if o not in (0, np.inf, None) else 0, 2) for o in vals]
                        df = pd.DataFrame([odds_row, invest_row], index=['Odds','Investment'], columns=cols)
                        model = self.pool_models[m]
                        model.setDataFrame(df)
                        # set diff highlighting if available
                        diff_df = core_logic.core_state.diff_dict.get(m, pd.DataFrame())
                        if not diff_df.empty:
                            last_diff = diff_df.iloc[-1]
                            # map column labels to values
                            highlight = {str(col): last_diff[col] for col in last_diff.index}
                            model.setHighlight(highlight)
                        else:
                            model.setHighlight(None)
                else:
                    vals = odds.get(m, [])
                    inv_df = core_logic.core_state.investment_dict.get(m, pd.DataFrame())
                    if vals:
                        combs = [c for c,_ in vals]
                        odds_vals = [v for _,v in vals]
                        inv_row = None
                        if not inv_df.empty:
                            inv_row = inv_df.iloc[-1].tolist()
                        else:
                            pool = investments.get(m, [None])[0] if investments.get(m) else None
                            if pool is not None:
                                inv_row = [round(pool / 1000 / float(o) if o not in (0, np.inf, None) else 0, 2) for o in odds_vals]
                        df = pd.DataFrame([odds_vals, inv_row], index=['Odds','Investment'], columns=combs)
                        model = self.pool_models[m]
                        model.setDataFrame(df)
                        diff_df = core_logic.core_state.diff_dict.get(m, pd.DataFrame())
                        if not diff_df.empty:
                            last_diff = diff_df.iloc[-1]
                            highlight = {col: last_diff[col] for col in last_diff.index}
                            model.setHighlight(highlight)
                        else:
                            model.setHighlight(None)
            except Exception as e:
                self.append_status(f'Error updating table for {m}: {e}')
        # update age tab or show message
        age_df = result.get('age')
        if age_df is not None:
            # place in a dedicated tab named 'AGE' if exists
            if 'AGE' not in self.pool_models:
                tbl = QTableView(); model = PandasModel(age_df.reset_index())
                self.pool_tabs.addTab(tbl, 'AGE')
                self.pool_tables['AGE'] = tbl
                self.pool_models['AGE'] = model
                tbl.setModel(model)
            else:
                self.pool_models['AGE'].setDataFrame(age_df.reset_index())
        self.append_status('UI updated')

    @Slot(str)
    def on_scrape_error(self, err):
        self.append_status('Error: ' + err)

    @Slot()
    def on_scrape_finished(self):
        self.append_status('Scrape finished')
        self.scrape_btn.setEnabled(True)
        self.progress.setVisible(False)

    @Slot()
    def on_start_auto(self):
        # start live update thread
        date = self.date_edit.date().toString('yyyy-MM-dd')
        place = self.place_combo.currentText()
        race_no = int(self.race_spin.value())
        methods = [m.strip() for m in (self.methods_input.text() or 'WIN,PLA').split(',') if m.strip()]
        # time_delay: use a default of 5 seconds or allow user to change later
        time_delay = 5.0
        self.live_thread = LiveUpdateWorker(date, place, race_no, methods, time_delay=time_delay)
        self.live_thread.update.connect(self.on_live_update)
        self.live_thread.status.connect(self.append_status)
        self.live_thread.error.connect(lambda e: self.append_status('Live error: ' + str(e)))
        self.live_thread.start()
        self.start_auto_btn.setEnabled(False)
        self.stop_auto_btn.setEnabled(True)
        self.append_status('Auto-update started')

    @Slot()
    def on_stop_auto(self):
        if hasattr(self, 'live_thread') and self.live_thread is not None:
            self.live_thread.stop()
            self.live_thread.wait(1000)
            self.append_status('Auto-update stopping')
        self.start_auto_btn.setEnabled(True)
        self.stop_auto_btn.setEnabled(False)

    @Slot(object)
    def on_live_update(self, result):
        self.append_status('Live update received')
        # reuse single-run result handling to update per-pool tabs
        try:
            self.on_scrape_result(result)
        except Exception as e:
            self.append_status('Error applying live update: ' + str(e))
    @Slot()
    def on_fetch_jockey(self):
        self.rank_output.append('Fetching jockey ranking...')
        df, err = core_logic.fetch_hkjc_jockey_ranking()
        if err:
            self.rank_output.append('Error: ' + err)
            return
        core_logic.core_state.jockey_ranking_df = df
        self.rank_output.append(df.to_string(index=False))

    @Slot()
    def on_fetch_trainer(self):
        self.rank_output.append('Fetching trainer ranking...')
        df, err = core_logic.fetch_hkjc_trainer_ranking()
        if err:
            self.rank_output.append('Error: ' + err)
            return
        core_logic.core_state.trainer_ranking_df = df
        self.rank_output.append(df.to_string(index=False))

    @Slot()
    def on_process(self):
        self.process_output.append('Running process & score (placeholder)...')
        try:
            res = {'summary': 'Example processed result. Replace with your logic.'}
            core_logic.core_state.latest_results = res
            self.process_output.append(str(res))
        except Exception as e:
            self.process_output.append('Error: ' + str(e))

    @Slot()
    def on_plot_bar(self):
        st = core_logic.core_state
        if st.latest_results and isinstance(st.latest_results, dict) and 'investments' in st.latest_results:
            investments = st.latest_results['investments']
            if investments.get('WIN'):
                arr = investments['WIN']
                df = pd.DataFrame([arr], columns=[str(i+1) for i in range(len(arr))])
                fig = core_logic.make_bar_figure(df, title='WIN sample')
                self.plot_canvas.display_figure(fig)
                return
        fig = core_logic.make_bar_figure(pd.DataFrame([[1,2,3]], columns=['1','2','3']), title='Sample')
        self.plot_canvas.display_figure(fig)

    @Slot()
    def on_plot_bubble(self):
        total = pd.DataFrame([[100,200,300]], columns=[1,2,3])
        deltaI = pd.Series([10,5,20])
        deltaQ = pd.Series([5,2,10])
        fig = core_logic.make_bubble_figure(total, deltaI, deltaQ, race_no=1, method_name=['WIN','QIN'])
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.html')
        try:
            pio.write_html(fig, tmp.name, full_html=True)
            self.plotly_view.load('file://' + tmp.name)
        finally:
            tmp.close()

    @Slot()
    def on_export(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Export latest results', 'results.txt', 'All Files (*)')
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(str(core_logic.core_state.latest_results))
            QMessageBox.information(self, 'Saved', f'Results saved to {path}')
        except Exception as e:
            QMessageBox.critical(self, 'Error', str(e))


if __name__ == '__main__':
    print('ui_components module loaded')
