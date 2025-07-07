#!/usr/bin/env python3


import sys
import os
import json
import sqlite3
import time
import threading
import platform
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import webbrowser

import psutil
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QVBoxLayout, QHBoxLayout,
    QWidget, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem,
    QProgressBar, QTextEdit, QComboBox, QDateEdit, QFrame,
    QSystemTrayIcon, QMenu, QMessageBox, QSplitter, QGroupBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QInputDialog,
    QContextMenuPolicy, QScrollArea
)
from PyQt6.QtCore import (
    QTimer, QThread, pyqtSignal, Qt, QDate, QSettings, QSize
)
from PyQt6.QtGui import (
    QFont, QPixmap, QIcon, QPalette, QColor, QAction, QPainter,
    QLinearGradient, QBrush
)
from PyQt6.QtChart import (
    QChart, QChartView, QPieSeries, QPieSlice, QLineSeries,
    QDateTimeAxis, QValueAxis, QBarSeries, QBarSet, QBarCategoryAxis
)

# Platform-specific imports
if platform.system() == "Windows":
    try:
        import win32gui
        import win32process
        import win32con
        WINDOWS_AVAILABLE = True
    except ImportError:
        WINDOWS_AVAILABLE = False
elif platform.system() == "Linux":
    try:
        import subprocess
        LINUX_AVAILABLE = True
    except ImportError:
        LINUX_AVAILABLE = False
else:
    WINDOWS_AVAILABLE = False
    LINUX_AVAILABLE = False


class ActivityTracker(QThread):
    """Background thread for tracking system activity"""
    
    activity_detected = pyqtSignal(dict)
    
    def __init__(self):
        super().__init__()
        self.running = False
        self.idle_threshold = 300  # 5 minutes in seconds
        self.last_activity_time = time.time()
        self.current_session = None
        
    def run(self):
        self.running = True
        while self.running:
            try:
                # Get active window and process info
                active_window = self.get_active_window()
                if active_window:
                    self.activity_detected.emit(active_window)
                    self.last_activity_time = time.time()
                
                # Check for idle time
                if time.time() - self.last_activity_time > self.idle_threshold:
                    self.activity_detected.emit({
                        'type': 'idle',
                        'timestamp': datetime.now().isoformat()
                    })
                
                time.sleep(1)  # Check every second
                
            except Exception as e:
                print(f"Activity tracker error: {e}")
                time.sleep(5)
    
    def get_active_window(self) -> Optional[Dict]:
        """Get information about the currently active window"""
        try:
            if platform.system() == "Windows" and WINDOWS_AVAILABLE:
                return self._get_windows_active_window()
            elif platform.system() == "Linux" and LINUX_AVAILABLE:
                return self._get_linux_active_window()
            else:
                return self._get_generic_active_process()
        except Exception as e:
            print(f"Error getting active window: {e}")
            return None
    
    def _get_windows_active_window(self) -> Optional[Dict]:
        """Windows-specific active window detection"""
        try:
            hwnd = win32gui.GetForegroundWindow()
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            process = psutil.Process(pid)
            window_title = win32gui.GetWindowText(hwnd)
            
            return {
                'type': 'window',
                'process_name': process.name(),
                'window_title': window_title,
                'pid': pid,
                'timestamp': datetime.now().isoformat()
            }
        except Exception:
            return None
    
    def _get_linux_active_window(self) -> Optional[Dict]:
        """Linux-specific active window detection"""
        try:
            # Try multiple methods for different window managers
            result = None
            
            # Try xprop (X11)
            try:
                output = subprocess.check_output(['xprop', '-root', '_NET_ACTIVE_WINDOW'], 
                                               stderr=subprocess.DEVNULL)
                window_id = output.decode().split()[-1]
                
                if window_id != '0x0':
                    title_output = subprocess.check_output(['xprop', '-id', window_id, 'WM_NAME'],
                                                         stderr=subprocess.DEVNULL)
                    window_title = title_output.decode().split('"')[1] if '"' in title_output.decode() else "Unknown"
                    
                    pid_output = subprocess.check_output(['xprop', '-id', window_id, '_NET_WM_PID'],
                                                       stderr=subprocess.DEVNULL)
                    pid = int(pid_output.decode().split()[-1])
                    
                    process = psutil.Process(pid)
                    return {
                        'type': 'window',
                        'process_name': process.name(),
                        'window_title': window_title,
                        'pid': pid,
                        'timestamp': datetime.now().isoformat()
                    }
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass
            
            # Try hyprctl (Hyprland)
            try:
                output = subprocess.check_output(['hyprctl', 'activewindow'], 
                                               stderr=subprocess.DEVNULL)
                lines = output.decode().strip().split('\n')
                
                title = "Unknown"
                pid = None
                
                for line in lines:
                    if line.startswith('title:'):
                        title = line.split(':', 1)[1].strip()
                    elif line.startswith('pid:'):
                        pid = int(line.split(':', 1)[1].strip())
                
                if pid:
                    process = psutil.Process(pid)
                    return {
                        'type': 'window',
                        'process_name': process.name(),
                        'window_title': title,
                        'pid': pid,
                        'timestamp': datetime.now().isoformat()
                    }
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass
                
            return None
            
        except Exception:
            return None
    
    def _get_generic_active_process(self) -> Optional[Dict]:
        """Generic method using psutil for basic process detection"""
        try:
            # Get the process with highest CPU usage as a fallback
            processes = []
            for proc in psutil.process_iter(['pid', 'name', 'cpu_percent']):
                try:
                    pinfo = proc.info
                    if pinfo['cpu_percent'] > 0:
                        processes.append(pinfo)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            
            if processes:
                active_proc = max(processes, key=lambda x: x['cpu_percent'])
                return {
                    'type': 'process',
                    'process_name': active_proc['name'],
                    'window_title': active_proc['name'],
                    'pid': active_proc['pid'],
                    'timestamp': datetime.now().isoformat()
                }
            
        except Exception:
            pass
        
        return None
    
    def stop(self):
        self.running = False
        self.wait()


class DatabaseManager:
    """Handles all database operations for storing activity data"""
    
    def __init__(self, db_path: str = "hwtracker.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize the database with required tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Activity sessions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS activity_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                process_name TEXT NOT NULL,
                window_title TEXT,
                start_time TEXT NOT NULL,
                end_time TEXT,
                duration INTEGER DEFAULT 0,
                tag TEXT,
                is_idle BOOLEAN DEFAULT 0
            )
        """)
        
        # Daily summaries table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                total_active_time INTEGER,
                total_idle_time INTEGER,
                most_used_app TEXT,
                session_count INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        conn.close()
    
    def log_activity(self, activity: Dict):
        """Log activity data to database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO activity_sessions 
            (process_name, window_title, start_time, is_idle)
            VALUES (?, ?, ?, ?)
        """, (
            activity.get('process_name', 'Unknown'),
            activity.get('window_title', ''),
            activity.get('timestamp'),
            activity.get('type') == 'idle'
        ))
        
        conn.commit()
        conn.close()
    
    def get_sessions_by_date(self, date: str) -> List[Dict]:
        """Get all sessions for a specific date"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM activity_sessions 
            WHERE date(start_time) = ? 
            ORDER BY start_time
        """, (date,))
        
        sessions = []
        for row in cursor.fetchall():
            sessions.append({
                'id': row[0],
                'process_name': row[1],
                'window_title': row[2],
                'start_time': row[3],
                'end_time': row[4],
                'duration': row[5],
                'tag': row[6],
                'is_idle': row[7]
            })
        
        conn.close()
        return sessions
    
    def get_app_usage_stats(self, days: int = 7) -> Dict:
        """Get application usage statistics"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        cursor.execute("""
            SELECT process_name, SUM(duration) as total_time, COUNT(*) as session_count
            FROM activity_sessions
            WHERE start_time >= ? AND start_time <= ? AND is_idle = 0
            GROUP BY process_name
            ORDER BY total_time DESC
        """, (start_date.isoformat(), end_date.isoformat()))
        
        stats = {}
        for row in cursor.fetchall():
            stats[row[0]] = {
                'total_time': row[1],
                'session_count': row[2]
            }
        
        conn.close()
        return stats
    
    def update_session_tag(self, session_id: int, tag: str):
        """Update the tag for a specific session"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE activity_sessions 
            SET tag = ? 
            WHERE id = ?
        """, (tag, session_id))
        
        conn.commit()
        conn.close()


class StatsWidget(QWidget):
    """Widget for displaying usage statistics"""
    
    def __init__(self, db_manager: DatabaseManager):
        super().__init__()
        self.db_manager = db_manager
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout()
        
        # Time range selector
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Time Range:"))
        
        self.time_range = QComboBox()
        self.time_range.addItems(["Today", "This Week", "This Month", "All Time"])
        self.time_range.currentTextChanged.connect(self.update_stats)
        controls.addWidget(self.time_range)
        
        controls.addStretch()
        layout.addLayout(controls)
        
        # Stats display
        self.stats_table = QTableWidget()
        self.stats_table.setColumnCount(3)
        self.stats_table.setHorizontalHeaderLabels(["Application", "Time Used", "Sessions"])
        self.stats_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.stats_table)
        
        self.setLayout(layout)
        self.update_stats()
    
    def update_stats(self):
        """Update the statistics display"""
        range_text = self.time_range.currentText()
        days = {"Today": 1, "This Week": 7, "This Month": 30, "All Time": 365}
        
        stats = self.db_manager.get_app_usage_stats(days.get(range_text, 7))
        
        self.stats_table.setRowCount(len(stats))
        
        for i, (app, data) in enumerate(stats.items()):
            self.stats_table.setItem(i, 0, QTableWidgetItem(app))
            
            # Format time nicely
            seconds = data['total_time']
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            time_str = f"{hours}h {minutes}m"
            
            self.stats_table.setItem(i, 1, QTableWidgetItem(time_str))
            self.stats_table.setItem(i, 2, QTableWidgetItem(str(data['session_count'])))


class TimelineWidget(QWidget):
    """Widget for displaying timeline of activities"""
    
    def __init__(self, db_manager: DatabaseManager):
        super().__init__()
        self.db_manager = db_manager
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout()
        
        # Date selector
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Date:"))
        
        self.date_edit = QDateEdit()
        self.date_edit.setDate(QDate.currentDate())
        self.date_edit.dateChanged.connect(self.update_timeline)
        controls.addWidget(self.date_edit)
        
        controls.addStretch()
        layout.addLayout(controls)
        
        # Timeline display
        self.timeline_widget = QTreeWidget()
        self.timeline_widget.setHeaderLabels(["Time", "Application", "Window Title", "Duration", "Tag"])
        self.timeline_widget.setContextMenuPolicy(QContextMenuPolicy.CustomContextMenu)
        self.timeline_widget.customContextMenuRequested.connect(self.show_context_menu)
        
        layout.addWidget(self.timeline_widget)
        
        self.setLayout(layout)
        self.update_timeline()
    
    def update_timeline(self):
        """Update the timeline display"""
        selected_date = self.date_edit.date().toString("yyyy-MM-dd")
        sessions = self.db_manager.get_sessions_by_date(selected_date)
        
        self.timeline_widget.clear()
        
        for session in sessions:
            if session['is_idle']:
                continue
                
            item = QTreeWidgetItem()
            start_time = datetime.fromisoformat(session['start_time'])
            item.setText(0, start_time.strftime("%H:%M:%S"))
            item.setText(1, session['process_name'])
            item.setText(2, session['window_title'] or "")
            
            # Format duration
            duration = session['duration'] or 0
            minutes = duration // 60
            seconds = duration % 60
            item.setText(3, f"{minutes}m {seconds}s")
            
            item.setText(4, session['tag'] or "")
            item.setData(0, Qt.ItemDataRole.UserRole, session['id'])
            
            self.timeline_widget.addTopLevelItem(item)
    
    def show_context_menu(self, position):
        """Show context menu for timeline items"""
        item = self.timeline_widget.itemAt(position)
        if not item:
            return
        
        menu = QMenu()
        tag_action = menu.addAction("Add/Edit Tag")
        
        action = menu.exec(self.timeline_widget.mapToGlobal(position))
        
        if action == tag_action:
            self.edit_tag(item)
    
    def edit_tag(self, item):
        """Edit tag for a timeline item"""
        current_tag = item.text(4)
        tag, ok = QInputDialog.getText(self, "Edit Tag", "Enter tag:", text=current_tag)
        
        if ok:
            session_id = item.data(0, Qt.ItemDataRole.UserRole)
            self.db_manager.update_session_tag(session_id, tag)
            item.setText(4, tag)


class DashboardWidget(QWidget):
    """Main dashboard widget showing today's activity"""
    
    def __init__(self, db_manager: DatabaseManager):
        super().__init__()
        self.db_manager = db_manager
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout()
        
        # Header
        header = QLabel("Today's Activity")
        header.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        layout.addWidget(header)
        
        # Quick stats
        stats_layout = QHBoxLayout()
        
        self.active_time_label = QLabel("Active Time: 0h 0m")
        self.session_count_label = QLabel("Sessions: 0")
        self.current_app_label = QLabel("Current: None")
        
        stats_layout.addWidget(self.active_time_label)
        stats_layout.addWidget(self.session_count_label)
        stats_layout.addWidget(self.current_app_label)
        stats_layout.addStretch()
        
        layout.addLayout(stats_layout)
        
        # Charts area
        charts_splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # Pie chart for app usage
        self.pie_chart = QChart()
        self.pie_chart.setTitle("App Usage Distribution")
        self.pie_chart_view = QChartView(self.pie_chart)
        charts_splitter.addWidget(self.pie_chart_view)
        
        # Recent activity list
        recent_group = QGroupBox("Recent Activity")
        recent_layout = QVBoxLayout()
        
        self.recent_list = QTreeWidget()
        self.recent_list.setHeaderLabels(["Time", "Application", "Window"])
        self.recent_list.setMaximumHeight(200)
        recent_layout.addWidget(self.recent_list)
        
        recent_group.setLayout(recent_layout)
        charts_splitter.addWidget(recent_group)
        
        layout.addWidget(charts_splitter)
        
        self.setLayout(layout)
        
        # Update timer
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_dashboard)
        self.update_timer.start(5000)  # Update every 5 seconds
        
        self.update_dashboard()
    
    def update_dashboard(self):
        """Update dashboard with current data"""
        today = datetime.now().strftime("%Y-%m-%d")
        sessions = self.db_manager.get_sessions_by_date(today)
        
        # Calculate stats
        active_sessions = [s for s in sessions if not s['is_idle']]
        total_time = sum(s['duration'] or 0 for s in active_sessions)
        
        hours = total_time // 3600
        minutes = (total_time % 3600) // 60
        
        self.active_time_label.setText(f"Active Time: {hours}h {minutes}m")
        self.session_count_label.setText(f"Sessions: {len(active_sessions)}")
        
        # Update pie chart
        self.update_pie_chart(active_sessions)
        
        # Update recent activity
        self.update_recent_activity(active_sessions[-10:])  # Last 10 sessions
    
    def update_pie_chart(self, sessions):
        """Update the pie chart with session data"""
        self.pie_chart.removeAllSeries()
        
        if not sessions:
            return
        
        # Group by app
        app_times = {}
        for session in sessions:
            app = session['process_name']
            duration = session['duration'] or 0
            app_times[app] = app_times.get(app, 0) + duration
        
        if not app_times:
            return
        
        # Create pie series
        series = QPieSeries()
        
        # Sort by usage time
        sorted_apps = sorted(app_times.items(), key=lambda x: x[1], reverse=True)
        
        # Take top 5 apps
        colors = ["#FF6B6B", "#4ECDC4", "#45B7D1", "#96CEB4", "#FFEAA7"]
        
        for i, (app, duration) in enumerate(sorted_apps[:5]):
            slice = series.append(app, duration)
            if i < len(colors):
                slice.setBrush(QBrush(QColor(colors[i])))
        
        # Add "Others" if more than 5 apps
        if len(sorted_apps) > 5:
            others_time = sum(duration for _, duration in sorted_apps[5:])
            if others_time > 0:
                slice = series.append("Others", others_time)
                slice.setBrush(QBrush(QColor("#DDD")))
        
        self.pie_chart.addSeries(series)
    
    def update_recent_activity(self, sessions):
        """Update the recent activity list"""
        self.recent_list.clear()
        
        for session in reversed(sessions):  # Most recent first
            if session['is_idle']:
                continue
                
            item = QTreeWidgetItem()
            start_time = datetime.fromisoformat(session['start_time'])
            item.setText(0, start_time.strftime("%H:%M"))
            item.setText(1, session['process_name'])
            item.setText(2, session['window_title'] or "")
            
            self.recent_list.addTopLevelItem(item)
    
    def update_current_app(self, app_name: str):
        """Update the current app label"""
        self.current_app_label.setText(f"Current: {app_name}")


class HwTrackerMainWindow(QMainWindow):
    """Main application window"""
    
    def __init__(self):
        super().__init__()
        self.db_manager = DatabaseManager()
        self.activity_tracker = ActivityTracker()
        self.current_session = None
        self.last_activity = None
        
        self.setup_ui()
        self.setup_system_tray()
        self.setup_activity_tracking()
        
        # Load settings
        self.settings = QSettings("HwTracker", "HwTracker")
        self.load_settings()
    
    def setup_ui(self):
        """Setup the main UI"""
        self.setWindowTitle("HwTracker - Track yo system, not your soul")
        self.setMinimumSize(1000, 700)
        
        # Apply dark theme
        self.setStyleSheet("""
            QMainWindow {
                background-color: #2b2b2b;
                color: #ffffff;
            }
            QTabWidget::pane {
                border: 1px solid #555;
                background-color: #3b3b3b;
            }
            QTabBar::tab {
                background-color: #4b4b4b;
                color: #ffffff;
                padding: 8px 16px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background-color: #6b6b6b;
            }
            QLabel {
                color: #ffffff;
            }
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QTreeWidget, QTableWidget {
                background-color: #3b3b3b;
                color: #ffffff;
                border: 1px solid #555;
            }
            QComboBox {
                background-color: #4b4b4b;
                color: #ffffff;
                border: 1px solid #555;
                padding: 4px;
            }
        """)
        
        # Central widget with tabs
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        layout = QVBoxLayout()
        
        # Header with logo and donation
        header = QHBoxLayout()
        
        title_label = QLabel("⚙️ HwTracker")
        title_label.setFont(QFont("Arial", 20, QFont.Weight.Bold))
        header.addWidget(title_label)
        
        subtitle_label = QLabel("\"Track yo system, not your soul.\"")
        subtitle_label.setFont(QFont("Arial", 10))
        subtitle_label.setStyleSheet("color: #aaa;")
        header.addWidget(subtitle_label)
        
        header.addStretch()
        
        # Donation button
        donate_btn = QPushButton("☕ Support MalikHw47")
        donate_btn.clicked.connect(self.open_donation_link)
        donate_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF5722;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #E64A19;
            }
        """)
        header.addWidget(donate_btn)
        
        layout.addLayout(header)
        
        # Add separator
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(separator)
        
        # Tab widget
        self.tabs = QTabWidget()
        
        # Dashboard tab
        self.dashboard = DashboardWidget(self.db_manager)
        self.tabs.addTab(self.dashboard, "📊 Today")
        
        # Timeline tab
        self.timeline = TimelineWidget(self.db_manager)
        self.tabs.addTab(self.timeline, "📅 Timeline")
        
        # Stats tab
        self.stats = StatsWidget(self.db_manager)
        self.tabs.addTab(self.stats, "📈 App Stats")
        
        # Settings tab
        self.settings_widget = self.create_settings_widget()
        self.tabs.addTab(self.settings_widget, "⚙️ Settings")
        
        layout.addWidget(self.tabs)
        
        central_widget.setLayout(layout)
        
        # Status bar
        self.statusBar().showMessage("Ready - Activity tracking started")
    
    def create_settings_widget(self) -> QWidget:
        """Create the settings widget"""
        widget = QWidget()
        layout = QVBoxLayout()
        
        # App info
        info_group = QGroupBox("About")
        info_layout = QVBoxLayout()
        
        info_layout.addWidget(QLabel("HwTracker v1.0"))
        info_layout.addWidget(QLabel("A privacy-focused system activity tracker"))
        info_layout.addWidget(QLabel("Created by MalikHw47"))
        
        info_group.setLayout(info_layout)
        layout.addWidget(info_group)
        
        # Settings
        settings_group = QGroupBox("Settings")
        settings_layout = QVBoxLayout()
        
        # Idle threshold
        idle_layout = QHBoxLayout()
        idle_layout.addWidget(QLabel("Idle threshold (minutes):"))
        self.idle_threshold_combo = QComboBox()
        self.idle_threshold_combo.addItems(["1", "5", "10", "15", "30"])
        self.idle_threshold_combo.setCurrentText("5")
        idle_layout.addWidget(self.idle_threshold_combo)
        idle_layout.addStretch()
        settings_layout.addLayout(idle_layout)
        
        # Auto-start
        auto_start_layout = QHBoxLayout()
        auto_start_layout.addWidget(QLabel("Start with system (requires admin):"))
        self.auto_start_btn = QPushButton("Enable")
        self.auto_start_btn.clicked.connect(self.toggle_auto_start)
        auto_start_layout.addWidget(self.auto_start_btn)
        auto_start_layout.addStretch()
        settings_layout.addLayout(auto_start_layout)
        
        settings_group.setLayout(settings_layout)
        layout.addWidget(settings_group)
        
        # Data management
        data_group = QGroupBox("Data Management")
        data_layout = QVBoxLayout()
        
        export_btn = QPushButton("Export Data")
        export_btn.clicked.connect(self.export_data)
        data_layout.addWidget(export_btn)
        
        clear_btn = QPushButton("Clear All Data")
        clear_btn.clicked.connect(self.clear_data)
        clear_btn.setStyleSheet("background-color: #f44336;")
        data_layout.addWidget(clear_btn)
        
        data_group.setLayout(data_layout)
        layout.addWidget(data_group)
        
        layout.addStretch()
        widget.setLayout(layout)
        
        return widget
    
    def setup_system_tray(self):
        """Setup system tray icon"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon))
        
        tray_menu = QMenu()
        
        show_action = QAction("Show", self)
        show_action.triggered.connect(self.show)
        tray_menu.addAction(show_action)
        
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.quit_application)
        tray_menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()
        
        self.tray_icon.activated.connect(self.tray_icon_activated)
    
    def tray_icon_activated(self, reason):
        """Handle tray icon activation"""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show()
            self.raise_()
            self.activateWindow()
    
    def setup_activity_tracking(self):
        """Setup activity tracking thread"""
        self.activity_tracker.activity_detected.connect(self.handle_activity)
        self.activity_tracker.start()
    
    def handle_activity(self, activity: Dict):
        """Handle detected activity"""
        if activity.get('type') == 'idle':
            self.end_current_session()
            self.statusBar().showMessage("Idle detected")
            return
        
        # Check if we need to start a new session
        if not self.current_session or self.is_different_activity(activity):
            self.end_current_session()
            self.start_new_session(activity)
        
        # Update current activity display
        if 'process_name' in activity:
            self.dashboard.update_current_app(activity['process_name'])
            self.statusBar().showMessage(f"Tracking: {activity['process_name']}")
        
        self.last_activity = activity
    
    def is_different_activity(self, activity: Dict) -> bool:
        """Check if the activity represents a different session"""
        if not self.last_activity:
            return True
        
        return (activity.get('process_name') != self.last_activity.get('process_name') or
                activity.get('window_title') != self.last_activity.get('window_title'))
    
    def start_new_session(self, activity: Dict):
        """Start a new activity session"""
        self.current_session = {
            'start_time': datetime.now(),
            'activity': activity
        }
        
        # Log to database
        self.db_manager.log_activity(activity)
    
    def end_current_session(self):
        """End the current activity session"""
        if not self.current_session:
            return
        
        # Calculate duration and update database
        duration = int((datetime.now() - self.current_session['start_time']).total_seconds())
        
        # Update the database with duration
        # (In a real implementation, you'd want to store session IDs and update them)
        
        self.current_session = None
    
    def open_donation_link(self):
        """Open the donation link"""
        webbrowser.open("https://www.ko-fi.com/MalikHw47")
    
    def toggle_auto_start(self):
        """Toggle auto-start functionality"""
        QMessageBox.information(self, "Auto-start", 
                               "Auto-start functionality requires system-specific implementation.\n"
                               "This would typically involve creating registry entries (Windows) "
                               "or desktop files (Linux).")
    
    def export_data(self):
        """Export activity data"""
        try:
            # Export as JSON
            today = datetime.now().strftime("%Y-%m-%d")
            sessions = self.db_manager.get_sessions_by_date(today)
            
            filename = f"hwtracker_export_{today}.json"
            with open(filename, 'w') as f:
                json.dump(sessions, f, indent=2)
            
            QMessageBox.information(self, "Export Complete", 
                                   f"Data exported to {filename}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export data: {e}")
    
    def clear_data(self):
        """Clear all stored data"""
        reply = QMessageBox.question(self, "Clear Data", 
                                    "Are you sure you want to clear all data?\n"
                                    "This action cannot be undone.",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                conn = sqlite3.connect(self.db_manager.db_path)
                cursor = conn.cursor()
                cursor.execute("DELETE FROM activity_sessions")
                cursor.execute("DELETE FROM daily_summaries")
                conn.commit()
                conn.close()
                
                QMessageBox.information(self, "Data Cleared", "All data has been cleared.")
                
                # Refresh displays
                self.dashboard.update_dashboard()
                self.timeline.update_timeline()
                self.stats.update_stats()
                
            except Exception as e:
                QMessageBox.critical(self, "Clear Error", f"Failed to clear data: {e}")
    
    def load_settings(self):
        """Load application settings"""
        # Load window geometry
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        
        # Load idle threshold
        idle_threshold = self.settings.value("idle_threshold", 5)
        self.idle_threshold_combo.setCurrentText(str(idle_threshold))
        self.activity_tracker.idle_threshold = int(idle_threshold) * 60
    
    def save_settings(self):
        """Save application settings"""
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("idle_threshold", self.idle_threshold_combo.currentText())
    
    def closeEvent(self, event):
        """Handle window close event"""
        if self.tray_icon and self.tray_icon.isVisible():
            self.hide()
            event.ignore()
            
            # Show tray message first time
            if not hasattr(self, '_tray_message_shown'):
                self.tray_icon.showMessage(
                    "HwTracker",
                    "Application is still running in the system tray.",
                    QSystemTrayIcon.MessageIcon.Information,
                    2000
                )
                self._tray_message_shown = True
        else:
            self.quit_application()
    
    def quit_application(self):
        """Quit the application"""
        self.save_settings()
        self.activity_tracker.stop()
        QApplication.quit()


class HwTrackerApp(QApplication):
    """Main application class"""
    
    def __init__(self, argv):
        super().__init__(argv)
        
        # Set application properties
        self.setApplicationName("HwTracker")
        self.setApplicationVersion("1.0")
        self.setOrganizationName("MalikHw47")
        
        # Create main window
        self.main_window = HwTrackerMainWindow()
        
        # Show window
        self.main_window.show()
    
    def run(self):
        """Run the application"""
        return self.exec()


def main():
    """Main entry point"""
    # Check dependencies
    missing_deps = []
    
    try:
        import PyQt6
    except ImportError:
        missing_deps.append("PyQt6")
    
    try:
        import psutil
    except ImportError:
        missing_deps.append("psutil")
    
    if missing_deps:
        print("Missing dependencies:")
        for dep in missing_deps:
            print(f"  - {dep}")
        print("\nInstall with:")
        print(f"pip install {' '.join(missing_deps)}")
        return 1
    
    # Create and run application
    app = HwTrackerApp(sys.argv)
    return app.run()


if __name__ == "__main__":
    sys.exit(main())


# Installation instructions:
"""
its made for MikuOS btw, so... :)
"""
