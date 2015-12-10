"""
    Flowblade Movie Editor is a nonlinear video editor.
    Copyright 2012 Janne Liljeblad.

    This file is part of Flowblade Movie Editor <http://code.google.com/p/flowblade>.

    Flowblade Movie Editor is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    Flowblade Movie Editor is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with Flowblade Movie Editor. If not, see <http://www.gnu.org/licenses/>.
"""

from gi.repository import GObject, GLib
from gi.repository import Gtk, Gdk, GdkPixbuf
from gi.repository import GdkX11
from gi.repository import Pango

import cairo
import locale
import mlt
import numpy as np
import os
import shutil
import subprocess
import sys
import time
import xml.dom.minidom

import appconsts
import cairoarea
import dialogutils
import editorstate
import editorpersistance
import gui
import guicomponents
import guiutils
import glassbuttons
import mltenv
import mltprofiles
import mlttransitions
import mltfilters
import positionbar
import respaths
import renderconsumer
import translations
import threading
import utils

import gmicplayer


MONITOR_WIDTH = 450
MONITOR_HEIGHT = 300 # initial value this gets changed when material is loaded
CLIP_FRAMES_DIR = "/clip_frames"
PREVIEW_FILE = "preview.png"

GMIC_SCRIPT_NODE = "gmicscript"

_scripts = None

_current_fps = None

_window = None
_player = None
_frame_writer = None
_current_preview_surface = None
_current_dimensions = None
_current_fps = None

def launch_gmic():
    print "Launch gmic..."
    gui.save_current_colors()
    
    FLOG = open(utils.get_hidden_user_dir_path() + "log_gmic", 'w')
    subprocess.Popen([sys.executable, respaths.LAUNCH_DIR + "flowbladegmic"], stdin=FLOG, stdout=FLOG, stderr=FLOG)


def main(root_path, force_launch=False):
       
    gtk_version = "%s.%s.%s" % (Gtk.get_major_version(), Gtk.get_minor_version(), Gtk.get_micro_version())
    editorstate.gtk_version = gtk_version
    try:
        editorstate.mlt_version = mlt.LIBMLT_VERSION
    except:
        editorstate.mlt_version = "0.0.99" # magic string for "not found"
        
    # Set paths.
    respaths.set_paths(root_path)

    load_preset_scripts_xml()
    
    #c Init gmic tool session dirs
    if os.path.exists(get_session_folder()):
        shutil.rmtree(get_session_folder())
        
    os.mkdir(get_session_folder())
    
    init_clip_frames_dir()
    
    # Load editor prefs and list of recent projects
    editorpersistance.load()
    if editorpersistance.prefs.dark_theme == True:
        respaths.apply_dark_theme()

    # Init translations module with translations data
    translations.init_languages()
    translations.load_filters_translations()
    mlttransitions.init_module()

    # Init gtk threads
    Gdk.threads_init()
    Gdk.threads_enter()

    # Request dark them if so desired
    if editorpersistance.prefs.dark_theme == True:
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)

    repo = mlt.Factory().init()

    # Set numeric locale to use "." as radix, MLT initilizes this to OS locale and this causes bugs 
    locale.setlocale(locale.LC_NUMERIC, 'C')

    # Check for codecs and formats on the system
    mltenv.check_available_features(repo)
    renderconsumer.load_render_profiles()

    # Load filter and compositor descriptions from xml files.
    mltfilters.load_filters_xml(mltenv.services)
    mlttransitions.load_compositors_xml(mltenv.transitions)

    # Create list of available mlt profiles
    mltprofiles.load_profile_list()

    gui.load_current_colors()
    
    global _window
    _window = GmicWindow()
    
    #gui.set_theme_colors()
    _window.pos_bar.set_dark_bg_color()
    
    os.putenv('SDL_WINDOWID', str(_window.monitor.get_window().get_xid()))
    Gdk.flush()
        
    Gtk.main()
    Gdk.threads_leave()
    
def load_preset_scripts_xml():
    presets_doc = xml.dom.minidom.parse(respaths.GMIC_SCRIPTS_DOC)

    global _scripts

    _scripts = []
    script_nodes = presets_doc.getElementsByTagName(GMIC_SCRIPT_NODE)
    for script_node in script_nodes:
        script = GmicScript(script_node)
        _scripts.append(script)

def get_session_folder():
    return utils.get_hidden_user_dir_path() + appconsts.GMIC_DIR + "/test"

def get_clip_frames_dir():
    return get_session_folder() + CLIP_FRAMES_DIR

def get_current_frame_file():
    return get_clip_frames_dir() + "/frame" + str(_player.current_frame()) + ".png"

def get_preview_file():
    return get_session_folder() + PREVIEW_FILE
    
def init_clip_frames_dir():
    if os.path.exists(get_clip_frames_dir()):
        shutil.rmtree(get_clip_frames_dir())
    os.mkdir(get_clip_frames_dir())
    
def open_clip_dialog(callback):
    
    file_select = Gtk.FileChooserDialog(_("Select Image Media"), _window, Gtk.FileChooserAction.OPEN,
                                    (Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                                    Gtk.STOCK_OPEN, Gtk.ResponseType.OK))

    file_select.set_default_response(Gtk.ResponseType.CANCEL)
    file_select.set_select_multiple(False)

    media_filter = utils.get_media_source_file_filter(False)
    all_filter = Gtk.FileFilter()
    all_filter.set_name(_("All files"))
    all_filter.add_pattern("*.*")
    file_select.add_filter(media_filter)
    file_select.add_filter(all_filter)

    if ((editorpersistance.prefs.open_in_last_opended_media_dir == True) 
        and (editorpersistance.prefs.last_opened_media_dir != None)):
        file_select.set_current_folder(editorpersistance.prefs.last_opened_media_dir)
    
    file_select.connect('response', callback)

    file_select.set_modal(True)
    file_select.show()

def _open_files_dialog_cb(file_select, response_id):
    filenames = file_select.get_filenames()
    file_select.destroy()

    if response_id != Gtk.ResponseType.OK:
        return
    if len(filenames) == 0:
        return

    new_profile = gmicplayer.set_current_profile(filenames[0])
    global _current_dimensions, _current_fps
    _current_dimensions = (new_profile.width(), new_profile.height(), 1.0)
    _current_fps = float(new_profile.frame_rate_num())/float(new_profile.frame_rate_den())

    global _player, _frame_writer
    _player = gmicplayer.GmicPlayer(filenames[0])
    _frame_writer = gmicplayer.FrameWriter(filenames[0])

    #display_aspect_num(self): return _mlt.Profile_display_aspect_num(self)
    #def display_aspect_den(self):
    _window.set_fps()
    _window.init_for_new_clip(filenames[0])
    _window.set_monitor_sizes()
    _window.set_widgets_sensitive(True)
    _player.create_sdl_consumer()
    _player.connect_and_start()

def show_preview():
    write_out_current_frame()
    
def write_out_current_frame():
    if os.path.exists(get_current_frame_file()):
        return

    _frame_writer.write_frame(get_clip_frames_dir() + "/", _player.current_frame())
    render_current_frame_preview()
    _window.preview_monitor.queue_draw()
    
def render_current_frame_preview():
    
    renderer = GmicPreviewRendererer()
    renderer.start()
    
    """
    shutil.copyfile(get_current_frame_file(), get_preview_file())
    
    # gmic 00012.jpg -gimp_charcoal 65,70,170,0,1,0,50,70,255,255,255,0,0,0,0,0 -output gmic_test2.png
    script_str = "gmic " + get_current_frame_file() + " -gimp_charcoal 65,70,170,0,1,0,50,70,255,255,255,0,0,0,0,0 -output " +  get_preview_file()
    print "Render preview:", script_str
    subprocess.call(script_str, shell=True)
     
    global _current_preview_surface
    _current_preview_surface = cairo.ImageSurface.create_from_png(get_preview_file())
    """

def prev_pressed():
    _player.seek_delta(-1)
    update_frame_displayers()
        
def next_pressed():
    _player.seek_delta(1)
    update_frame_displayers()

def mark_in_pressed():
    _player.producer.mark_in = _player.current_frame()
    _window.update_marks_display()
    _window.pos_bar.update_display_from_producer(_player.producer)

def mark_out_pressed():
    _player.producer.mark_out = _player.current_frame()
    _window.update_marks_display()
    _window.pos_bar.update_display_from_producer(_player.producer)
    
def marks_clear_pressed():
    _player.producer.mark_in = -1
    _player.producer.mark_out = -1
    update_frame_displayers()
        
def to_mark_in_pressed():
    if _player.producer.mark_in != -1:
        _player.seek_frame(_player.producer.mark_in)
    update_frame_displayers()
    
def to_mark_out_pressed():
    if _player.producer.mark_out != -1:
        _player.seek_frame(_player.producer.mark_out)
    update_frame_displayers()

def update_frame_displayers():
    frame = _player.current_frame()
    _window.tc_display.set_frame(frame)
    _window.pos_bar.update_display_from_producer(_player.producer)
    

class GmicWindow(Gtk.Window):
    def __init__(self):
        GObject.GObject.__init__(self)
        self.connect("delete-event", lambda w, e:_shutdown())

        app_icon = GdkPixbuf.Pixbuf.new_from_file(respaths.IMAGE_PATH + "flowblademedialinker.png")
        self.set_icon(app_icon)

        # Load media row
        load_button = Gtk.Button(_("Load Clip"))
        load_button.connect("clicked",
                            lambda w: self.load_button_clicked())
        self.media_info = Gtk.Label()
        self.media_info.set_markup("<small>no clip loaded</small>")#"<small>" + "video_clip.mpg, 1920x1080,  25.0fps" + "</small>" )
        load_row = Gtk.HBox(False, 2)
        load_row.pack_start(load_button, False, False, 0)
        load_row.pack_start(guiutils.get_pad_label(6, 2), False, False, 0)
        load_row.pack_start(self.media_info, False, False, 0)
        load_row.pack_start(Gtk.Label(), True, True, 0)
        load_row.set_margin_bottom(4)

        # Clip monitor
        black_box = Gtk.EventBox()
        black_box.add(Gtk.Label())
        bg_color = Gdk.Color(red=0.0, green=0.0, blue=0.0)
        black_box.modify_bg(Gtk.StateType.NORMAL, bg_color)
        self.monitor = black_box  # This could be any GTK+ widget (that is not "windowless"), only its XWindow draw rect 
                                  # is used to position and scale SDL overlay that actually displays video.
        self.monitor.set_size_request(MONITOR_WIDTH, MONITOR_HEIGHT)

        left_vbox = Gtk.VBox(False, 0)
        left_vbox.pack_start(load_row, False, False, 0)
        left_vbox.pack_start(self.monitor, True, True, 0)

        self.preview_info = Gtk.Label()
        self.preview_info.set_markup("<small>" + _("no preview") + "</small>" )
        preview_info_row = Gtk.HBox()
        preview_info_row.pack_start(self.preview_info, False, False, 0)
        preview_info_row.pack_start(Gtk.Label(), True, True, 0)
        preview_info_row.set_margin_top(6)
        preview_info_row.set_margin_bottom(8)

        self.preview_monitor = cairoarea.CairoDrawableArea2(MONITOR_WIDTH, MONITOR_HEIGHT, self._draw_preview)

        right_vbox = Gtk.VBox(False, 2)
        right_vbox.pack_start(preview_info_row, False, False, 0)
        right_vbox.pack_start(self.preview_monitor, True, True, 0)


        # Monitors panel
        monitors_panel = Gtk.HBox(False, 2)
        monitors_panel.pack_start(left_vbox, False, False, 0)
        monitors_panel.pack_start(Gtk.Label(), True, True, 0)
        monitors_panel.pack_start(right_vbox, False, False, 0)

        # Control row
        self.tc_display = guicomponents.MonitorTCDisplay()
        self.tc_display.use_internal_frame = True
        self.tc_display.widget.set_valign(Gtk.Align.CENTER)
        self.tc_display.use_internal_fps = True
        
        self.pos_bar = positionbar.PositionBar(False)
        self.pos_bar.set_listener(self.position_listener)
        pos_bar_frame = Gtk.Frame()
        pos_bar_frame.add(self.pos_bar.widget)
        pos_bar_frame.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
        pos_bar_frame.set_margin_top(5)
        pos_bar_frame.set_margin_bottom(4)
        pos_bar_frame.set_margin_left(6)
        pos_bar_frame.set_margin_right(2)
        
        self.control_buttons = glassbuttons.GmicButtons()
        pressed_callback_funcs = [prev_pressed,
                                  next_pressed,
                                  mark_in_pressed,
                                  mark_out_pressed,
                                  marks_clear_pressed,
                                  to_mark_in_pressed,
                                  to_mark_out_pressed]
        self.control_buttons.set_callbacks(pressed_callback_funcs)
        
        self.preview_button = Gtk.Button(_("Preview"))
        self.preview_button.connect("clicked",
                            lambda w: self.preview_button_clicked())
                            
        control_panel = Gtk.HBox(False, 2)
        control_panel.pack_start(self.tc_display.widget, False, False, 0)
        control_panel.pack_start(pos_bar_frame, True, True, 0)
        control_panel.pack_start(self.control_buttons.widget, False, False, 0)
        control_panel.pack_start(guiutils.pad_label(2, 2), False, False, 0)
        control_panel.pack_start(self.preview_button, False, False, 0)

        preview_panel = Gtk.VBox(False, 2)
        preview_panel.pack_start(monitors_panel, False, False, 0)
        preview_panel.pack_start(control_panel, False, False, 0)
        preview_panel.set_margin_bottom(8)

        # Script area
        self.preset_label = Gtk.Label("Preset Script:")
        
        self.preset_select = Gtk.ComboBoxText()
        self.preset_select.set_tooltip_text(_("Select Preset G'Mic script"))
        for gmic_script in _scripts:
            self.preset_select.append_text(gmic_script.name)
            print gmic_script.script
        self.preset_select.set_active(0)

        preset_row = Gtk.HBox(False, 2)
        preset_row.pack_start(self.preset_label, False, False, 0)
        preset_row.pack_start(guiutils.pad_label(6, 12), False, False, 0)
        preset_row.pack_start(self.preset_select, False, False, 0)
        preset_row.pack_start(Gtk.Label(), True, True, 0)

        self.script_view = Gtk.TextView()
        self.script_view.set_sensitive(False)
        self.script_view.set_pixels_above_lines(2)
        self.script_view.set_left_margin(2)

        script_sw = Gtk.ScrolledWindow()
        script_sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        script_sw.add(self.script_view)
        script_sw.set_size_request(MONITOR_WIDTH - 100, 125)

        self.out_view = Gtk.TextView()
        self.out_view.set_sensitive(False)
        self.out_view.set_pixels_above_lines(2)
        self.out_view.set_left_margin(2)
        fd = Pango.FontDescription.from_string("Sans 8")
        self.out_view.override_font(fd)

        out_sw = Gtk.ScrolledWindow()
        out_sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        out_sw.add(self.out_view)
        out_sw.set_size_request(MONITOR_WIDTH - 150, 100)
        
        script_vbox = Gtk.VBox(False, 2)
        script_vbox.pack_start(preset_row, False, False, 0)
        script_vbox.pack_start(script_sw, True, True, 0)
        script_vbox.pack_start(out_sw, True, True, 0)

        # Render panel
        self.mark_in_label = guiutils.bold_label("Mark In:")
        self.mark_out_label = guiutils.bold_label("Mark Out:")
        self.length_label = guiutils.bold_label("Length:")
        
        self.mark_in_info = Gtk.Label("-")
        self.mark_out_info = Gtk.Label("-")
        self.length_info = Gtk.Label("-")

        in_row = guiutils.get_two_column_box(self.mark_in_label, self.mark_in_info, 150)
        out_row = guiutils.get_two_column_box(self.mark_out_label, self.mark_out_info, 150)
        length_row = guiutils.get_two_column_box(self.length_label, self.length_info, 150)
        
        marks_row = Gtk.VBox(False, 2)
        marks_row.pack_start(in_row, True, True, 0)
        marks_row.pack_start(out_row, True, True, 0)
        marks_row.pack_start(length_row, True, True, 0)

        self.out_folder = Gtk.FileChooserButton(_("Select Folder"))
        self.out_folder.set_action(Gtk.FileChooserAction.SELECT_FOLDER)
        self.out_folder.set_current_folder(os.path.expanduser("~") + "/")
        self.out_label = Gtk.Label(label=_("Frames Folder:"))
        out_folder_row = guiutils.get_left_justified_box([self.out_label, guiutils.pad_label(12, 2), self.out_folder])

        self.encode_check_label = Gtk.Label("Encode Video")
        self.encode_check = Gtk.CheckButton()
        self.encode_check.set_active(False)
        
        self.encode_settings_button = Gtk.Button(_("Encoding settings"))
        self.encode_desc = Gtk.Label()
        self.encode_desc.set_markup("<small>"+ "MPEG-2, 3000kbps" + "</small>")
        
        encode_row = Gtk.HBox(False, 2)
        encode_row.pack_start(self.encode_check, False, False, 0)
        encode_row.pack_start(self.encode_check_label, False, False, 0)
        encode_row.pack_start(guiutils.pad_label(48, 12), False, False, 0)
        encode_row.pack_start(self.encode_settings_button, False, False, 0)
        encode_row.pack_start(guiutils.pad_label(6, 12), False, False, 0)
        encode_row.pack_start(self.encode_desc, False, False, 0)
        encode_row.pack_start(Gtk.Label(), True, True, 0)
        encode_row.set_margin_bottom(6)

        self.file_name_label = Gtk.Label(_("Name:"))
        self.movie_name = Gtk.Entry()
        self.movie_name.set_text("movie")
        self.extension_label = Gtk.Label(".mpg")
        
        video_file_row = Gtk.HBox(False, 2)
        video_file_row.pack_start(self.file_name_label, False, False, 0)
        video_file_row.pack_start(self.movie_name, False, False, 0)
        video_file_row.pack_start(self.extension_label, False, False, 0)
        video_file_row.pack_start(Gtk.Label(), True, True, 0)
        
        self.render_percentage = Gtk.Label("0%")
        
        self.render_status_info = Gtk.Label()
        self.render_status_info.set_markup("<small>"+ "52 frames, requiring 768MB dis space, video file: ../movie.mpg" + "</small>")

        render_status_row = Gtk.HBox(False, 2)
        render_status_row.pack_start(self.render_percentage, False, False, 0)
        render_status_row.pack_start(Gtk.Label(), True, True, 0)
        render_status_row.pack_start(self.render_status_info, False, False, 0)

        render_status_row.set_margin_bottom(6)

        self.render_progress_bar = Gtk.ProgressBar()
        self.render_progress_bar.set_valign(Gtk.Align.CENTER)

        self.stop_button = guiutils.get_sized_button(_("Stop"), 100, 32)
        self.render_button = guiutils.get_sized_button(_("Render"), 100, 32)

        render_row = Gtk.HBox(False, 2)
        render_row.pack_start(self.render_progress_bar, True, True, 0)
        render_row.pack_start(guiutils.pad_label(12, 2), False, False, 0)
        render_row.pack_start(self.stop_button, False, False, 0)
        render_row.pack_start(self.render_button, False, False, 0)

        render_vbox = Gtk.VBox(False, 2)
        render_vbox.pack_start(marks_row, False, False, 0)
        render_vbox.pack_start(Gtk.Label(), True, True, 0)
        render_vbox.pack_start(encode_row, False, False, 0)
        render_vbox.pack_start(Gtk.Label(), True, True, 0)
        render_vbox.pack_start(out_folder_row, False, False, 0)
        render_vbox.pack_start(Gtk.Label(), True, True, 0)
        render_vbox.pack_start(render_status_row, False, False, 0)
        render_vbox.pack_start(render_row, False, False, 0)
        render_vbox.pack_start(guiutils.pad_label(24, 24), False, False, 0)
        
        # Script work panel
        script_work_panel = Gtk.HBox(False, 2)
        script_work_panel.pack_start(script_vbox, False, False, 0)
        script_work_panel.pack_start(guiutils.pad_label(12, 2), False, False, 0)
        script_work_panel.pack_start(render_vbox, True, True, 0)

        self.load_script = Gtk.Button(_("Load Script"))
        #load_layers.connect("clicked", lambda w:self._load_layers_pressed())
        self.save_script = Gtk.Button(_("Save Script"))
        #save_layers.connect("clicked", lambda w:self._save_layers_pressed())

        info_b = guiutils.get_sized_button(_("Info"), 150, 32)
        exit_b = guiutils.get_sized_button(_("Close"), 150, 32)
        
        editor_buttons_row = Gtk.HBox()
        editor_buttons_row.pack_start(self.load_script, False, False, 0)
        editor_buttons_row.pack_start(self.save_script, False, False, 0)
        editor_buttons_row.pack_start(Gtk.Label(), True, True, 0)
        editor_buttons_row.pack_start(info_b, False, False, 0)
        editor_buttons_row.pack_start(guiutils.pad_label(96, 2), False, False, 0)
        editor_buttons_row.pack_start(exit_b, False, False, 0)

        # Build window
        pane = Gtk.VBox(False, 2)
        pane.pack_start(preview_panel, False, False, 0)
        pane.pack_start(script_work_panel, False, False, 0)
        pane.pack_start(editor_buttons_row, False, False, 0)

        align = guiutils.set_margins(pane, 12, 12, 12, 12)

        # Set pane and show window
        self.add(align)
        self.set_title(_("G'MIC Effects"))
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_widgets_sensitive(False)
        self.show_all()
        self.set_resizable(False)
        self.set_active_state(False)

    def init_for_new_clip(self, clip_path):
        self.clip_path = clip_path
        self.set_active_state(True)
        self.pos_bar.update_display_from_producer(_player.producer)
        self.media_info.set_markup("<small>" + os.path.basename(clip_path) + "</small>")

    def update_marks_display(self):
        if _player.producer.mark_in == -1:
            self.mark_in_info.set_text("-")
        else:
            self.mark_in_info.set_text(utils.get_tc_string_with_fps(_player.producer.mark_in, _current_fps))
        
        if  _player.producer.mark_out == -1:
            self.mark_out_info.set_text("-")
        else:
            self.mark_out_info.set_text(utils.get_tc_string_with_fps(_player.producer.mark_out, _current_fps))

        self.mark_in_info.queue_draw()
        self.mark_out_info.queue_draw()

    def load_button_clicked(self):
        open_clip_dialog(_open_files_dialog_cb)

    def preview_button_clicked(self):
        show_preview()

    def set_active_state(self, active):
        self.monitor.set_sensitive(active)
        self.pos_bar.widget.set_sensitive(active)

    def set_fps(self):
        self.tc_display.fps = _current_fps
        
    def position_listener(self, normalized_pos, length):
        frame = int(normalized_pos * length)
        self.tc_display.set_frame(frame)
        _player.seek_frame(frame)
        self.pos_bar.widget.queue_draw()

    def _draw_preview(self, event, cr, allocation):
        x, y, w, h = allocation

        if _current_preview_surface != None:
            width, height, pixel_aspect = _current_dimensions
            scale = float(MONITOR_WIDTH) / float(width)
            print "scale", scale
            cr.scale(scale * pixel_aspect, scale)
            cr.set_source_surface(_current_preview_surface, 0, 0)
            cr.paint()
        else:
            cr.set_source_rgb(0.5, 0.5, 0.5)
            cr.rectangle(0, 0, w, h)
            cr.fill()

    def set_monitor_sizes(self):
        w, h, pixel_aspect = _current_dimensions
        new_height = MONITOR_WIDTH * (float(h)/float(w)) * pixel_aspect
        self.monitor.set_size_request(MONITOR_WIDTH, new_height)
        self.preview_monitor.set_size_request(MONITOR_WIDTH, new_height)

    def set_widgets_sensitive(self, value):
        self.monitor.set_sensitive(value)
        self.preview_info.set_sensitive(value)
        self.preview_monitor.set_sensitive(value)
        self.tc_display.widget.set_sensitive(value)
        self.pos_bar.widget.set_sensitive(value)      
        self.control_buttons.set_sensitive(value)
        self.preset_label.set_sensitive(value)
        self.preset_select.set_sensitive(value)
        self.script_view.set_sensitive(value) 
        self.out_view.set_sensitive(value)       
        self.mark_in_info.set_sensitive(value)
        self.mark_out_info.set_sensitive(value)
        self.length_info.set_sensitive(value)
        self.out_folder.set_sensitive(value)
        self.encode_check_label.set_sensitive(value)
        self.encode_check.set_sensitive(value)
        self.encode_settings_button.set_sensitive(value)
        self.encode_desc.set_sensitive(value)
        self.file_name_label.set_sensitive(value)
        self.movie_name.set_sensitive(value)
        self.extension_label.set_sensitive(value)       
        self.render_percentage.set_sensitive(value)
        self.render_status_info.set_sensitive(value)
        self.render_progress_bar.set_sensitive(value)
        self.stop_button.set_sensitive(value)
        self.render_button.set_sensitive(value)
        self.preview_button.set_sensitive(value)
        self.load_script.set_sensitive(value)
        self.save_script.set_sensitive(value)
        self.mark_in_label.set_sensitive(value)
        self.mark_out_label.set_sensitive(value)
        self.length_label.set_sensitive(value)
        self.out_label.set_sensitive(value)
        self.media_info.set_sensitive(value)
 


class GmicPreviewRendererer(threading.Thread):

    def __init__(self):
        threading.Thread.__init__(self)

    def run(self):
        start_time = time.time()
        _window.preview_info.set_markup("<small>" + _("Rendering preview...") + "</small>" )
            
        shutil.copyfile(get_current_frame_file(), get_preview_file())
        
        # gmic 00012.jpg -gimp_charcoal 65,70,170,0,1,0,50,70,255,255,255,0,0,0,0,0 -output gmic_test2.png
        script_str = "gmic " + get_current_frame_file() + " -gimp_charcoal 65,70,170,0,1,0,50,70,255,255,255,0,0,0,0,0 -output " +  get_preview_file()
        script_str = "gmic " + get_current_frame_file() + " -glow 10% -output " +  get_preview_file()
        script_str = "gmic " + get_current_frame_file() + " -ditheredbw  -output " +  get_preview_file()
        script_str = "gmic " + get_current_frame_file() + " -blur_angular 10 -output " +  get_preview_file()
        script_str = "gmic " + get_current_frame_file() + " -rodilius 20,5,200,17,2,1 -output " +  get_preview_file()
        script_str = "gmic " + get_current_frame_file() + " -circlism 2,10 -output " +  get_preview_file()
        script_str = "gmic " + get_current_frame_file() + " -cartoon 3,200,20,0.25,1.5,8,0 -output " +  get_preview_file()
        script_str = "gmic " + get_current_frame_file() + " -gimp_circle_abstraction 8,5,0.8,0,1,1,1,0 -output " +  get_preview_file()
        script_str = "gmic " + get_current_frame_file() + " -gimp_feltpen 300,50,1,0.1,20,5,0 -output " +  get_preview_file()
        script_str = "gmic " + get_current_frame_file() + " -gimp_pen_drawing 10,0 -output " +  get_preview_file()
        script_str = "gmic " + get_current_frame_file() + " -gimp_polygonize 300,10,10,10,10,0,0,0,255,0 -output " +  get_preview_file()
        script_str = "gmic " + get_current_frame_file() + " -gimp_poster_hope 0,3,0 -output " +  get_preview_file()

        print "Render preview:", script_str
        #out = subprocess.check_output(script_str, stdin=subprocess.STDIN, shell=True)
        


        FLOG = open(utils.get_hidden_user_dir_path() + "log_gmic_preview", 'w')
        p = subprocess.Popen(script_str, shell=True, stdin=FLOG, stdout=FLOG, stderr=FLOG)
        p.wait()
        FLOG.close()
    
        # read log
        f = open(utils.get_hidden_user_dir_path() + "log_gmic_preview", 'r')
        out = f.read()
        f.close()

        global _current_preview_surface
        _current_preview_surface = cairo.ImageSurface.create_from_png(get_preview_file())

        _window.out_view.get_buffer().set_text(out)

        render_time = time.time() - start_time
        time_str = "{0:.2f}".format(round(render_time,2))
        _window.preview_info.set_markup("<small>" + _("Preview for frame: ") + \
            utils.get_tc_string_with_fps(_player.current_frame(), _current_fps) + ", render time: " + time_str +  "</small>" )
            
        _window.preview_monitor.queue_draw()


class GmicScript:
    """
    Info of a filter (mlt.Service) that is is available to the user.
    Constructor input is a dom node object.
    This is used to create FilterObject objects.
    """
    def __init__(self, script_node):
        self.name = script_node.getElementsByTagName("name").item(0).firstChild.nodeValue
        self.script = script_node.getElementsByTagName("script").item(0).firstChild.nodeValue
        