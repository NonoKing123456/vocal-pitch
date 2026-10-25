"""Scrollable piano roll with pitch-centered zoom, waveform, selection and playback cursor."""
import math
import numpy as np
from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import QAbstractScrollArea, QFrame, QScrollBar, QStyle, QStyleOptionSlider
from pitch_core import note_name
from recording_analysis import prepare_waveform
from tonality import pitch_classes

class NavigationScrollBar(QScrollBar):
    """Melodyne-style thumb-end zoom, center dragging and double-click fit."""
    def __init__(self,orientation,roll):
        super().__init__(orientation,roll)
        self.roll=roll
        self.edge=None
        self.setMouseTracking(True)

    def parts(self):
        option=QStyleOptionSlider();self.initStyleOption(option)
        slider=self.style().subControlRect(QStyle.ComplexControl.CC_ScrollBar,option,QStyle.SubControl.SC_ScrollBarSlider,self)
        groove=self.style().subControlRect(QStyle.ComplexControl.CC_ScrollBar,option,QStyle.SubControl.SC_ScrollBarGroove,self)
        return slider,groove

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            slider,groove=self.parts()
            horizontal=self.orientation()==Qt.Orientation.Horizontal
            coordinate=event.position().x() if horizontal else event.position().y()
            first=slider.left() if horizontal else slider.top()
            last=slider.right() if horizontal else slider.bottom()
            if abs(coordinate-first)<=6 or abs(coordinate-last)<=6:
                self.edge='start' if abs(coordinate-first)<abs(coordinate-last) else 'end'
                self.edge_origin=coordinate
                self.edge_length=max(1,groove.width() if horizontal else groove.height())
                r=self.roll
                if horizontal:
                    self.edge_bounds=(r.time_for_x(r.LEFT),r.time_for_x(r.viewport().width()))
                else:
                    self.edge_bounds=(r.midi_for_y(r.TOP),r.midi_for_y(r.TOP+r.grid_height))
                event.accept();return
        super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if self.edge:
            horizontal=self.orientation()==Qt.Orientation.Horizontal
            coordinate=event.position().x() if horizontal else event.position().y()
            delta=coordinate-self.edge_origin
            r=self.roll
            first,last=self.edge_bounds
            if horizontal:
                duration=r.clip.duration if r.clip else 12.
                change=delta*duration/self.edge_length
                minimum=r.grid_width/2000.
                if self.edge=='start':first=max(0.,min(last-minimum,first+change))
                else:last=min(duration,max(first+minimum,last+change))
                r.pixels_per_second=max(.3,min(2000.,r.grid_width/max(last-first,.001)))
                r.update_ranges();r.horizontalScrollBar().setValue(round(first*r.pixels_per_second))
            else:
                change=delta*(r.HIGH-r.LOW+1)/self.edge_length
                minimum=r.grid_height/240.
                if self.edge=='start':first=min(r.HIGH+.5,max(last+minimum,first-change))
                else:last=max(r.LOW-.5,min(first-minimum,last-change))
                r.row_height=max(8.,min(240.,r.grid_height/max(first-last,.01)))
                r.update_ranges();r.verticalScrollBar().setValue(round((r.HIGH+.5-first)*r.row_height))
            r.zoomChanged.emit();r.viewport().update();event.accept();return
        if not event.buttons():
            slider,_=self.parts()
            horizontal=self.orientation()==Qt.Orientation.Horizontal
            coordinate=event.position().x() if horizontal else event.position().y()
            first=slider.left() if horizontal else slider.top()
            last=slider.right() if horizontal else slider.bottom()
            self.setCursor((Qt.CursorShape.SizeHorCursor if horizontal else Qt.CursorShape.SizeVerCursor)
                if min(abs(coordinate-first),abs(coordinate-last))<=6 else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        self.edge=None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            self.roll.fit_axis(self.orientation());event.accept();return
        super().mouseDoubleClickEvent(event)

def time_label(seconds, detailed=False):
    seconds = max(0., seconds)
    minutes = int(seconds//60)
    remain = seconds-minutes*60
    return f'{minutes:02}:{remain:04.1f}' if detailed else f'{minutes:02}:{int(remain):02}'

class PianoRoll(QAbstractScrollArea):
    seekRequested = Signal(float)
    selectionChanged = Signal(object)
    selectionFinished = Signal()
    inspected = Signal(object)
    playbackRequested = Signal(float,bool)
    cycleToggleRequested = Signal()
    zoomChanged = Signal()
    LEFT, TOP, WAVE = 82., 44., 58.
    LOW, HIGH = 24, 108

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumSize(480, 280)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.colors = {}
        self.notation = 'sharp'
        self.tolerance = 5
        self.row_height = 28.
        self.pixels_per_second = 100.
        self.clip, self.track = None, None
        self.position = 0.
        self.selection = None
        self.hover_time = None
        self.drag_start = None
        self.drag_point = None
        self.pan_point = None
        self.paths = []
        self.key_tonic=None
        self.scale_pcs=None
        self.zoom_point=None
        self.ruler_point=None
        self.cycle_edge=None
        self.cycle_active=False
        self.previous_zoom=None
        self.side_scroll=False
        self.setHorizontalScrollBar(NavigationScrollBar(Qt.Orientation.Horizontal,self))
        self.setVerticalScrollBar(NavigationScrollBar(Qt.Orientation.Vertical,self))
        self.viewport().grabGesture(Qt.GestureType.PinchGesture)
        self.horizontalScrollBar().valueChanged.connect(lambda _: self.viewport().update())
        self.verticalScrollBar().valueChanged.connect(lambda _: self.viewport().update())

    @property
    def grid_height(self):
        return max(40., self.viewport().height()-self.TOP-self.WAVE)

    @property
    def grid_width(self):
        return max(40., self.viewport().width()-self.LEFT)

    def set_colors(self, colors, notation='sharp', tolerance=5):
        self.colors, self.notation, self.tolerance = colors, notation, tolerance
        self.viewport().update()

    def set_key(self,tonic=None,mode=None):
        self.key_tonic=tonic
        self.scale_pcs=pitch_classes(tonic,mode) if tonic is not None else None
        self.viewport().update()

    def set_data(self, clip, track):
        self.clip, self.track = clip, track
        self.position, self.selection = 0., None
        if clip.waveform is None:
            prepare_waveform(clip)
        self.paths = []
        path, previous = QPainterPath(), False
        for seconds, midi in zip(track.times, track.midi):
            if np.isfinite(midi):
                if previous:
                    path.lineTo(float(seconds), float(midi))
                else:
                    path.moveTo(float(seconds), float(midi))
                previous = True
            else:
                if not path.isEmpty():
                    self.paths.append(path)
                path, previous = QPainterPath(), False
        if not path.isEmpty():
            self.paths.append(path)
        self.fit_all()

    def update_ranges(self):
        total = self.clip.duration if self.clip else 12.
        self.horizontalScrollBar().setRange(0, max(0, math.ceil(total*self.pixels_per_second-self.grid_width)))
        self.horizontalScrollBar().setPageStep(round(self.grid_width))
        self.verticalScrollBar().setRange(0, max(0, math.ceil((self.HIGH-self.LOW+1)*self.row_height-self.grid_height)))
        self.verticalScrollBar().setPageStep(round(self.grid_height))
        self.verticalScrollBar().setSingleStep(round(self.row_height))

    def x_for_time(self, seconds):
        return self.LEFT+seconds*self.pixels_per_second-self.horizontalScrollBar().value()

    def time_for_x(self, x):
        value = (x-self.LEFT+self.horizontalScrollBar().value())/self.pixels_per_second
        return max(0., min(self.clip.duration if self.clip else 0., value))

    def y_for_midi(self, midi):
        return self.TOP+(self.HIGH+.5-midi)*self.row_height-self.verticalScrollBar().value()

    def midi_for_y(self, y):
        return self.HIGH+.5-(y-self.TOP+self.verticalScrollBar().value())/self.row_height

    def zoom_vertical(self, factor, anchor_y=None):
        anchor_y = self.TOP+self.grid_height/2 if anchor_y is None else float(anchor_y)
        pitch = self.midi_for_y(anchor_y)
        self.row_height = max(8., min(240., self.row_height*factor))
        self.update_ranges()
        self.verticalScrollBar().setValue(round((self.HIGH+.5-pitch)*self.row_height-(anchor_y-self.TOP)))
        self.zoomChanged.emit()
        self.viewport().update()

    def zoom_horizontal(self, factor, anchor_x=None):
        anchor_x = self.LEFT+self.grid_width/2 if anchor_x is None else float(anchor_x)
        seconds = self.time_for_x(anchor_x)
        self.pixels_per_second = max(.3, min(2000., self.pixels_per_second*factor))
        self.update_ranges()
        self.horizontalScrollBar().setValue(round(seconds*self.pixels_per_second-(anchor_x-self.LEFT)))
        self.zoomChanged.emit()
        self.viewport().update()

    def fit_all(self):
        duration = self.clip.duration if self.clip else 12.
        self.pixels_per_second = max(.3, min(2000., self.grid_width/max(duration,.1)))
        if self.track is not None and np.any(np.isfinite(self.track.midi)):
            voiced = self.track.midi[np.isfinite(self.track.midi)]
            lower, upper = float(np.percentile(voiced,1))-3, float(np.percentile(voiced,99))+3
            self.row_height = max(8., min(100., self.grid_height/max(upper-lower,8.)))
            center = (lower+upper)/2
        else:
            self.row_height, center = 28., 60.
        self.update_ranges()
        self.horizontalScrollBar().setValue(0)
        self.verticalScrollBar().setValue(round((self.HIGH+.5-center)*self.row_height-self.grid_height/2))
        self.zoomChanged.emit()
        self.viewport().update()

    def view_state(self):
        return self.pixels_per_second,self.row_height,self.horizontalScrollBar().value(),self.verticalScrollBar().value()

    def restore_view(self,state):
        self.pixels_per_second,self.row_height,horizontal,vertical=state
        self.update_ranges();self.horizontalScrollBar().setValue(horizontal);self.verticalScrollBar().setValue(vertical)
        self.zoomChanged.emit();self.viewport().update()

    def fit_axis(self,orientation):
        previous=self.view_state()
        self.fit_all()
        if orientation==Qt.Orientation.Horizontal:
            self.row_height=previous[1]
            if self.cycle_active and self.selection:
                a,b=self.selection
                self.pixels_per_second=max(.3,min(2000.,self.grid_width/max(b-a,.05)))
                self.update_ranges();self.horizontalScrollBar().setValue(round(a*self.pixels_per_second))
            self.update_ranges();self.verticalScrollBar().setValue(previous[3])
        else:
            self.pixels_per_second=previous[0]
            self.update_ranges();self.horizontalScrollBar().setValue(previous[2])
        self.viewport().update()

    def segment_at(self,point):
        if not self.track or point.x()<self.LEFT or point.y()<self.TOP:
            return None
        seconds=self.time_for_x(point.x())
        return next((s for s in self.track.segments if s.start<=seconds<=s.end
            and abs(point.y()-self.y_for_midi(s.center_midi))<=max(8,self.row_height*.5)),None)

    def focus_notes(self,point):
        hit=self.segment_at(point)
        if hit is None:
            if self.previous_zoom:
                self.restore_view(self.previous_zoom);self.previous_zoom=None
            return
        self.previous_zoom=self.view_state()
        notes=[hit]
        if self.selection and self.selection[0]<=hit.start<=self.selection[1]:
            notes=[s for s in self.track.segments if s.end>=self.selection[0] and s.start<=self.selection[1]]
        start=max(0.,min(s.start for s in notes)-.15)
        end=min(self.clip.duration,max(s.end for s in notes)+.15)
        lower=min(s.center_midi for s in notes)-1.5
        upper=max(s.center_midi for s in notes)+1.5
        self.pixels_per_second=max(.3,min(2000.,self.grid_width/max(.1,end-start)))
        self.row_height=max(8.,min(240.,self.grid_height/max(upper-lower,3.)))
        self.update_ranges()
        self.horizontalScrollBar().setValue(round(start*self.pixels_per_second))
        self.verticalScrollBar().setValue(round((self.HIGH+.5-upper)*self.row_height))
        self.zoomChanged.emit();self.viewport().update()

    def set_position(self, seconds, follow=False):
        self.position = seconds
        x = self.x_for_time(seconds)
        if follow and (x > self.viewport().width()-40 or x < self.LEFT):
            self.horizontalScrollBar().setValue(round(seconds*self.pixels_per_second-self.grid_width*.2))
        self.viewport().update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_ranges()

    def paintEvent(self, event):
        if not self.colors:
            return
        t, w, h = self.colors, self.viewport().width(), self.viewport().height()
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.viewport().rect(), QColor(t['bg']))
        font = QFont('Microsoft YaHei UI')
        font.setPixelSize(11)
        p.setFont(font)
        grid = QRectF(self.LEFT, self.TOP, self.grid_width, self.grid_height)
        p.save()
        p.setClipRect(grid)
        for midi in range(self.LOW, self.HIGH+1):
            y = self.y_for_midi(midi)
            if y+self.row_height/2 < self.TOP or y-self.row_height/2 > self.TOP+self.grid_height:
                continue
            black = midi%12 in (1,3,6,8,10)
            in_scale=self.scale_pcs is None or midi%12 in self.scale_pcs
            dark=QColor(t['text']).lightness()>128
            fill=t['surface'] if black else t['bg']
            if self.scale_pcs is not None:
                fill=(t['track'] if in_scale else t['bg']) if dark else (t['bg'] if in_scale else t['track'])
            p.fillRect(QRectF(self.LEFT, y-self.row_height/2, self.grid_width, self.row_height),
                       QColor(fill))
            if self.key_tonic is not None and midi%12==self.key_tonic:
                tonic=QColor(t['accent']);tonic.setAlpha(28 if dark else 20)
                p.fillRect(QRectF(self.LEFT,y-self.row_height/2,self.grid_width,self.row_height),tonic)
            pen = QColor(t['border'])
            pen.setAlpha(190 if midi%12 == 0 else 95)
            p.setPen(QPen(pen, 1))
            p.drawLine(QPointF(self.LEFT,y+self.row_height/2), QPointF(w,y+self.row_height/2))
            if self.row_height >= 64:
                pen.setAlpha(100)
                p.setPen(QPen(pen, 1, Qt.PenStyle.DotLine))
                p.drawLine(QPointF(self.LEFT,y), QPointF(w,y))
        if self.track is not None:
            visible_start = self.time_for_x(self.LEFT)
            visible_end = self.time_for_x(w)
            for segment in self.track.segments:
                if segment.end < visible_start or segment.start > visible_end:
                    continue
                x, end = self.x_for_time(segment.start), self.x_for_time(segment.end)
                y = self.y_for_midi(segment.center_midi)
                height = max(8., self.row_height*.60)
                in_scale=self.scale_pcs is None or segment.note%12 in self.scale_pcs
                color = QColor(t['good'] if in_scale and abs(segment.cents) <= self.tolerance else t['warn'])
                stroke = QColor(color)
                color.setAlpha(60)
                stroke.setAlpha(130)
                p.setBrush(color)
                p.setPen(QPen(stroke, 1))
                rect = QRectF(x,y-height/2,max(3.,end-x-1),height)
                p.drawRoundedRect(rect, min(7.,height/3),min(7.,height/3))
                if end-x > 60 and self.row_height >= 26:
                    p.setPen(QColor(t['text']))
                    label = note_name(segment.note,self.notation)
                    if end-x > 130:
                        label += f'  {segment.cents:+.1f}'
                    label_rect=QRectF(x+5,y-height/2-18,max(20.,end-x-8),16)
                    p.drawText(label_rect, Qt.AlignmentFlag.AlignVCenter, label)
            p.save()
            transform = QTransform(self.pixels_per_second,0,0,-self.row_height,
                self.LEFT-self.horizontalScrollBar().value(),
                self.TOP+(self.HIGH+.5)*self.row_height-self.verticalScrollBar().value())
            p.setTransform(transform)
            pen = QPen(QColor(t['accent']),2)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            for path in self.paths:
                p.drawPath(path)
            p.restore()
        p.restore()

        # Piano keys remain fixed while the musical grid scrolls horizontally.
        p.save()
        p.setClipRect(QRectF(0,self.TOP,self.LEFT,self.grid_height))
        active = self.track.at(self.position)[0] if self.track is not None and self.track.at(self.position) else None
        dark_theme = QColor(t['text']).lightness() > 128
        for midi in range(self.LOW,self.HIGH+1):
            y = self.y_for_midi(midi)
            if not self.TOP-self.row_height <= y <= self.TOP+self.grid_height+self.row_height:
                continue
            black = midi%12 in (1,3,6,8,10)
            in_scale=self.scale_pcs is None or midi%12 in self.scale_pcs
            white_color=('#d5dae5' if dark_theme else '#ffffff') if in_scale else ('#555b68' if dark_theme else '#b2bac8')
            p.fillRect(QRectF(0,y-self.row_height/2,self.LEFT,self.row_height), QColor(white_color))
            if black:
                p.fillRect(QRectF(0,y-self.row_height/2,self.LEFT-16,self.row_height),QColor('#454e63' if in_scale else '#20232b'))
            if self.key_tonic is not None and midi%12==self.key_tonic:
                p.fillRect(QRectF(0,y-self.row_height/2,4,self.row_height),QColor(t['accent']))
            if midi == active:
                c = QColor(t['accent']); c.setAlpha(150)
                p.fillRect(QRectF(0,y-self.row_height/2,self.LEFT-1,self.row_height),c)
            p.setPen(QPen(QColor('#8c96aa'),.6))
            p.drawLine(QPointF(0,y+self.row_height/2),QPointF(self.LEFT,y+self.row_height/2))
            if self.row_height >= 19 or midi%12 == 0:
                p.setPen(QColor(('#edf2fb' if black else '#263247') if in_scale else '#a1a8b7' if dark_theme else '#596273'))
                p.drawText(QRectF(5,y-10,self.LEFT-22 if black else self.LEFT-9,20),
                           Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,note_name(midi,self.notation))
        p.restore()

        p.fillRect(QRectF(0,0,w,self.TOP),QColor(t['surface']))
        p.fillRect(QRectF(0,h-self.WAVE,w,self.WAVE),QColor(t['surface']))
        intervals = [.1,.2,.5,1,2,5,10,30,60,120,300,600]
        interval = next((value for value in intervals if value*self.pixels_per_second >= 70),600)
        begin = math.floor(self.time_for_x(self.LEFT)/interval)*interval
        p.save()
        p.setClipRect(QRectF(self.LEFT,0,self.grid_width,h))
        seconds = begin
        while self.x_for_time(seconds) <= w:
            x = self.x_for_time(seconds)
            c = QColor(t['border']); c.setAlpha(100)
            p.setPen(QPen(c,1))
            p.drawLine(QPointF(x,self.TOP),QPointF(x,self.TOP+self.grid_height))
            p.setPen(QColor(t['secondary']))
            p.drawText(QRectF(x+5,1,90,26),Qt.AlignmentFlag.AlignVCenter,time_label(seconds,interval<1))
            seconds += interval
        if self.clip is not None and self.clip.waveform is not None:
            wave = self.clip.waveform
            baseline = h-self.WAVE/2
            p.setPen(QPen(QColor(t['secondary']),1))
            step = max(1., 1/self.pixels_per_second/self.clip.wave_step)
            for pixel in range(int(self.LEFT),min(w,math.ceil(self.x_for_time(self.clip.duration)))):
                first = int(self.time_for_x(pixel)/self.clip.wave_step)
                if first >= len(wave):
                    continue
                last = min(len(wave), first+math.ceil(step))
                low, high = float(np.min(wave[first:last,0])),float(np.max(wave[first:last,1]))
                p.drawLine(QPointF(pixel,baseline-low*(self.WAVE*.42)),QPointF(pixel,baseline-high*(self.WAVE*.42)))
        if self.selection:
            a,b = self.selection
            c = QColor(t['accent']); c.setAlpha(27)
            p.fillRect(QRectF(self.x_for_time(a),0,(b-a)*self.pixels_per_second,h),c)
            bar=QColor(t['accent']);bar.setAlpha(180 if self.cycle_active else 70)
            p.fillRect(QRectF(self.x_for_time(a),28,(b-a)*self.pixels_per_second,14),bar)
            p.setPen(QPen(QColor(t['accent']),1,Qt.PenStyle.DashLine))
            for value in (a,b):
                x=self.x_for_time(value);p.drawLine(QPointF(x,0),QPointF(x,h))
        if self.hover_time is not None:
            x=self.x_for_time(self.hover_time)
            c=QColor(t['secondary']);c.setAlpha(130)
            p.setPen(QPen(c,1,Qt.PenStyle.DotLine));p.drawLine(QPointF(x,self.TOP),QPointF(x,h))
        if self.clip:
            x=self.x_for_time(self.position)
            p.setPen(QPen(QColor(t['accent']),2));p.drawLine(QPointF(x,0),QPointF(x,h))
            p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor(t['accent']))
            p.drawPolygon(QPolygonF([QPointF(x-6,0),QPointF(x+6,0),QPointF(x,9)]))
            if self.track is not None:
                value=self.track.at(self.position)
                if value:
                    midi=69+12*math.log2(value[2]/self.track.a4)
                    p.setPen(QPen(QColor(t['bg']),2));p.setBrush(QColor(t['accent']))
                    p.drawEllipse(QPointF(x,self.y_for_midi(midi)),4,4)
        p.restore()
        if self.clip is None:
            p.setPen(QColor(t['secondary']))
            p.drawText(grid,Qt.AlignmentFlag.AlignCenter,'录音或导入音频')
        p.end()

    def mousePressEvent(self,event):
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if event.button() in (Qt.MouseButton.BackButton,Qt.MouseButton.ForwardButton):
            self.side_scroll=True;event.accept();return
        modifiers=event.modifiers()
        pan=modifiers&Qt.KeyboardModifier.ControlModifier and modifiers&Qt.KeyboardModifier.ShiftModifier
        zoom=modifiers&Qt.KeyboardModifier.ControlModifier and modifiers&Qt.KeyboardModifier.AltModifier
        if event.button()==Qt.MouseButton.MiddleButton or (event.button()==Qt.MouseButton.LeftButton and pan):
            self.pan_point=event.position();self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
        elif event.button()==Qt.MouseButton.LeftButton and zoom:
            self.zoom_point=event.position();self.zoom_anchor=event.position()
            self.viewport().setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif event.button() == Qt.MouseButton.LeftButton and self.clip and event.position().x() >= self.LEFT:
            seconds=self.time_for_x(event.position().x())
            if event.position().y()<28:
                self.ruler_point=event.position()
                self.seekRequested.emit(seconds)
            elif event.position().y()<self.TOP:
                self.cycle_origin=(seconds,self.selection)
                if self.selection and abs(event.position().x()-self.x_for_time(self.selection[0]))<=6:self.cycle_edge='left'
                elif self.selection and abs(event.position().x()-self.x_for_time(self.selection[1]))<=6:self.cycle_edge='right'
                elif self.selection and self.selection[0]<seconds<self.selection[1]:self.cycle_edge='body'
                else:self.cycle_edge='new';self.selection=(seconds,seconds)
            else:
                self.drag_start=seconds;self.drag_point=event.position()

    def mouseMoveEvent(self,event):
        if self.pan_point is not None:
            delta=event.position()-self.pan_point
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value()-round(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value()-round(delta.y()))
            self.pan_point=event.position()
        elif self.zoom_point is not None:
            delta=event.position()-self.zoom_point
            self.zoom_horizontal(math.exp(delta.x()/120),self.zoom_anchor.x())
            self.zoom_vertical(math.exp(-delta.y()/120),self.zoom_anchor.y())
            self.zoom_point=event.position()
        elif self.ruler_point is not None:
            delta=event.position()-self.ruler_point
            if abs(delta.y())>1:self.zoom_horizontal(math.exp(-delta.y()/100),self.ruler_point.x())
            if abs(delta.x())>1:self.seekRequested.emit(self.time_for_x(event.position().x()))
            self.ruler_point=event.position()
        elif self.cycle_edge:
            seconds=self.time_for_x(event.position().x())
            origin,old=self.cycle_origin
            if self.cycle_edge=='new':self.selection=tuple(sorted((origin,seconds)))
            elif self.cycle_edge=='left':self.selection=(min(seconds,old[1]-.05),old[1])
            elif self.cycle_edge=='right':self.selection=(old[0],max(seconds,old[0]+.05))
            else:
                offset=max(-old[0],min(self.clip.duration-old[1],seconds-origin))
                self.selection=(old[0]+offset,old[1]+offset)
            self.selectionChanged.emit(self.selection)
        elif self.clip and event.position().x() >= self.LEFT:
            value=self.time_for_x(event.position().x())
            self.hover_time=value
            if self.drag_start is not None and abs(event.position().x()-self.drag_point.x()) > 4:
                self.selection=tuple(sorted((self.drag_start,value)))
                self.selectionChanged.emit(self.selection)
            if self.track:
                self.inspected.emit((value,self.track.at(value)))
        else:
            self.hover_time=None
        if self.pan_point is None and self.zoom_point is None:
            modifiers=event.modifiers()
            if modifiers&Qt.KeyboardModifier.ControlModifier and modifiers&Qt.KeyboardModifier.ShiftModifier:
                self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            elif modifiers&Qt.KeyboardModifier.ControlModifier and modifiers&Qt.KeyboardModifier.AltModifier:
                self.viewport().setCursor(Qt.CursorShape.SizeFDiagCursor)
            else:self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        self.viewport().update()

    def mouseReleaseEvent(self,event):
        if event.button() in (Qt.MouseButton.BackButton,Qt.MouseButton.ForwardButton):
            self.side_scroll=False;event.accept();return
        if self.cycle_edge:
            if self.selection and self.selection[1]-self.selection[0]<.05:
                self.selection=None;self.selectionChanged.emit(None)
            else:self.selectionFinished.emit()
        if self.drag_start is not None:
            if abs(event.position().x()-self.drag_point.x()) <= 4:
                self.seekRequested.emit(self.time_for_x(event.position().x()))
            elif self.selection and self.selection[1]-self.selection[0] < .05:
                self.selection=None;self.selectionChanged.emit(None)
            else:
                self.selectionFinished.emit()
        self.drag_start,self.drag_point,self.pan_point=None,None,None
        self.zoom_point,self.ruler_point,self.cycle_edge=None,None,None
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)

    def mouseDoubleClickEvent(self,event):
        if not self.clip or event.position().x() < self.LEFT:
            return
        modifiers=event.modifiers()
        if modifiers&Qt.KeyboardModifier.ControlModifier and modifiers&(Qt.KeyboardModifier.AltModifier|Qt.KeyboardModifier.ShiftModifier):
            self.focus_notes(event.position());return
        value=self.time_for_x(event.position().x())
        if 28<=event.position().y()<self.TOP:
            self.cycleToggleRequested.emit();return
        if modifiers&Qt.KeyboardModifier.AltModifier:
            self.playbackRequested.emit(value,True);return
        segment=self.segment_at(event.position())
        if segment:
            self.selection=(segment.start,segment.end)
            self.selectionChanged.emit(self.selection)
            self.selectionFinished.emit()
            self.seekRequested.emit(segment.start)
            self.viewport().update()
        else:self.playbackRequested.emit(value,False)

    def leaveEvent(self,event):
        self.hover_time=None;self.inspected.emit(None);self.viewport().update()
        super().leaveEvent(event)

    def wheelEvent(self,event):
        modifiers=event.modifiers()
        angles,pixels=event.angleDelta(),event.pixelDelta()
        steps=(angles.y() or angles.x())/120
        if modifiers&Qt.KeyboardModifier.ControlModifier and modifiers&Qt.KeyboardModifier.AltModifier:
            self.zoom_horizontal(1.22**steps,event.position().x())
            self.zoom_vertical(1.22**steps,event.position().y())
        elif modifiers&Qt.KeyboardModifier.ShiftModifier or self.side_scroll or event.buttons()&(Qt.MouseButton.BackButton|Qt.MouseButton.ForwardButton):
            delta=pixels.y() or pixels.x() or ((angles.y() or angles.x())/120*100)
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value()-round(delta))
        else:
            dx=pixels.x() if not pixels.isNull() else angles.x()/120*100
            dy=pixels.y() if not pixels.isNull() else angles.y()/120*self.row_height*3
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value()-round(dx))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value()-round(dy))
        event.accept()

    def viewportEvent(self,event):
        if event.type()==event.Type.NativeGesture and event.gestureType()==Qt.NativeGestureType.ZoomNativeGesture:
            factor=max(.2,1+event.value())
            self.zoom_horizontal(factor,event.position().x());self.zoom_vertical(factor,event.position().y())
            return True
        if event.type()==event.Type.Gesture:
            gesture=event.gesture(Qt.GestureType.PinchGesture)
            if gesture:
                factor=gesture.scaleFactor()/max(gesture.lastScaleFactor(),.001)
                self.zoom_horizontal(factor);self.zoom_vertical(factor);return True
        return super().viewportEvent(event)
