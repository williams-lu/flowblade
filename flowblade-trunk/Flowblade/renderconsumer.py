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
    along with Flowblade Movie Editor.  If not, see <http://www.gnu.org/licenses/>.
"""

"""
Module contains objects and methods needed to create render consumers.
"""
import mlt
import time
import threading
import xml.dom.minidom

import mltenv
import respaths

# File describing existing encoding and quality options
RENDER_ENCODING_FILE = "/res/render/renderencoding.xml"

# Node, attribute names.
NAME = "name"
TYPE = "type"
ID = "id"
EXTENSION = "extension"
RESIZABLE = "resize"
ARGS = "args"
REPLACED_VALUES = "replvalues"
ADDED_ATTRIBUTES = "addargs"
BITRATE_OPTION = "boption"
QUALITY_GROUP = "qualityqroup"
ENCODING_OPTION = "encodingoption"
QGROUP = "qgroup"
DEFAULT_INDEX = "defaultindex"
PROFILE = "profile"
QUALITY = "quality"
BITRATE = "bitrate"
AUDIO_DESCRIPTION = "audiodesc"
NON_USER = "nonuser"

# Replace strings and attribute values
BITRATE_RPL = "%BITRATE%"
VARIABLE_VAL = "%VARIABLE%"
SCREEN_SIZE_RPL = "%SCREENSIZE%"
ASPECT_RPL = "%ASPECT%"

# Option strings
#SIZE_OPTION = "s"

render_encoding_doc = None
encoding_options = []
not_supported_encoding_options = []
quality_option_groups = {}
quality_option_groups_default_index = {}
non_user_encodings = []

# replace empty strings with None values
def _get_attribute(node, attr_name):
    value = node.getAttribute(attr_name)
    if value == "":
        return None
    
    return value
    
class QualityOption:
    """
    A render quality option for an EncodingOption.
    
    Values of mlt render consumer properties (usually bitrate) that equal 
    key expressions are replaced with corresponding values.
    """
    def __init__(self, quality_node):
        self.name = _get_attribute(quality_node, NAME)
        # Replaced render arguments
        replaced_values_str = _get_attribute(quality_node, REPLACED_VALUES)
        self.replaced_expressions = []
        self.replace_map = {}
        if replaced_values_str != None:
            tokens = replaced_values_str.split(";")
            for token in tokens:
                token_sides = token.split(" ")
                self.replaced_expressions.append(token_sides[0])
                self.replace_map[token_sides[0]] = token_sides[1]
        # Added render arguments
        added_atrrs_str = _get_attribute(quality_node, ADDED_ATTRIBUTES)
        self.add_map = {}
        if added_atrrs_str != None:
            tokens = added_atrrs_str.split(" ")
            for token in tokens:
                token_sides = token.split("=")
                self.add_map[token_sides[0]] = token_sides[1]

class EncodingOption:
    """
    An object that groups together vcodoc, acodec, format and quality options group.
    Object is used to set mlt render consumer properties.
    """
    def __init__(self, option_node):
        self.name = _get_attribute(option_node, NAME)
        self.type = _get_attribute(option_node, TYPE)
        self.resizable = (_get_attribute(option_node, RESIZABLE) == "True")
        self.extension = _get_attribute(option_node, EXTENSION)
        self.nonuser = _get_attribute(option_node, NON_USER)
        quality_qroup_id = _get_attribute(option_node, QGROUP)
        self.quality_options = quality_option_groups[quality_qroup_id]
        try:
            quality_default_index = int(quality_option_groups_default_index[quality_qroup_id])
        except KeyError:
            quality_default_index = None
        self.quality_default_index = quality_default_index
        self.audio_desc = _get_attribute(option_node, AUDIO_DESCRIPTION)
        profile_node = option_node.getElementsByTagName(PROFILE).item(0)
        self.attr_string =  _get_attribute(profile_node, ARGS)
        self.acodec = None
        self.vcodec = None
        self.format = None

        tokens = self.attr_string.split(" ")
        for token in tokens:
            token_sides = token.split("=")
            if token_sides[0] == "acodec":
                self.acodec = token_sides[1]
            elif token_sides[0] == "vcodec":
                self.vcodec = token_sides[1]
            elif token_sides[0] == "f":
                self.format = token_sides[1]


        self.supported, self.err_msg = mltenv.render_profile_supported(self.format, 
                                                         self.vcodec,
                                                         self.acodec)
                                                         
    def get_args_vals_tuples_list(self, profile, quality_option=None):
        # Encoding options
        tokens = self.attr_string.split(" ")
        args_tuples = []
        for token in tokens:
            # Get property keys and values
            token_sides = token.split("=")
            arg1 = str(token_sides[0])
            arg2 = str(token_sides[1])
            
            # Replace keyword values
            if arg2 == SCREEN_SIZE_RPL:
                arg2 = str(profile.width())+ "x" + str(profile.height())
            if arg2 == ASPECT_RPL:
                arg2 = "@" + str(profile.display_aspect_num()) + "/" + str(profile.display_aspect_den())

            # Replace keyword values from quality options values
            if quality_option != None:
                if arg2 in quality_option.replaced_expressions:
                    arg2 = str(quality_option.replace_map[arg2])
            args_tuples.append((arg1, arg2))
        
        return args_tuples

    def get_audio_description(self):
        if self.audio_desc == None:
            desc = "Not available"
        else:
            desc = self.audio_desc 
        return "<small>" + desc + "</small>"
    
def load_render_profiles():
    """
    Load render profiles from xml into DOM at start-up and build
    object tree.
    """
    file_path = respaths.ROOT_PATH + RENDER_ENCODING_FILE
    global render_encoding_doc
    render_encoding_doc = xml.dom.minidom.parse(file_path)

    # Create quality option groups
    global quality_option_groups
    qgroup_nodes = render_encoding_doc.getElementsByTagName(QUALITY_GROUP)
    for qgnode in qgroup_nodes:
        quality_qroup = []
        group_key = _get_attribute(qgnode, ID)
        group_default_index = _get_attribute(qgnode, DEFAULT_INDEX)
        if group_default_index != None: 
            quality_option_groups_default_index[group_key] = group_default_index
        option_nodes = qgnode.getElementsByTagName(QUALITY)
        for option_node in option_nodes:
            q_option = QualityOption(option_node)
            quality_qroup.append(q_option)
        quality_option_groups[group_key] = quality_qroup

    # Create encoding options
    print "Render profiles:"
    global encoding_options, not_supported_encoding_options, non_user_encodings
    encoding_option_nodes = render_encoding_doc.getElementsByTagName(ENCODING_OPTION)
    for eo_node in encoding_option_nodes:
        encoding_option = EncodingOption(eo_node)
        if encoding_option.supported:
            if encoding_option.nonuser == None:
                encoding_options.append(encoding_option)
                msg = "...available"
            else:
                non_user_encodings.append(encoding_option) 
                msg = "...available as non-user encoding"
        else:
            msg = "...NOT available, " + encoding_option.err_msg + " missing"
            not_supported_encoding_options.append(encoding_option)
        print encoding_option.name + msg
    
def get_render_consumer_for_encoding_and_quality(file_path, profile, enc_opt_index, quality_opt_index):
    encoding_option = encoding_options[enc_opt_index]
    quality_option = encoding_option.quality_options[quality_opt_index]

    # Encoding options key, value list
    args_vals_list = encoding_option.get_args_vals_tuples_list(profile, quality_option)

    # Quality options  key, value list
    for k, v in quality_option.add_map.iteritems():
        args_vals_list.append((str(k), str(v)))
        
    return get_mlt_render_consumer(file_path, profile, args_vals_list)

def get_render_condumer_for_encoding(file_path, profile, encoding_option):
    # Encoding options key, value list
    args_vals_list = encoding_option.get_args_vals_tuples_list(profile)
        
    return get_mlt_render_consumer(file_path, profile, args_vals_list)

def get_render_consumer_for_text_buffer(file_path, profile, buf):
    args_vals_list, error = get_ffmpeg_opts_args_vals_tuples_list(buf)
    if error != None:
        return (None, error)

    render_consumer = get_mlt_render_consumer(file_path, profile, args_vals_list)
    return (render_consumer, None)

def get_mlt_render_consumer(file_path, profile, args_vals_list):
    consumer = mlt.Consumer(profile, "avformat", file_path)
    consumer.set("real_time", -1)

    for arg_val in args_vals_list:
        k, v = arg_val
        consumer.set(str(k), str(v))
    
    return consumer
            
def get_ffmpeg_opts_args_vals_tuples_list(buf):
    end = buf.get_end_iter()
    arg_vals = []
    for i in range(0, buf.get_line_count()):
        line_start = buf.get_iter_at_line(i)
        if i == buf.get_line_count() - 1:
            line_end = end
        else:
            line_end = buf.get_iter_at_line(i + 1)
        av_tuple, error = _parse_line(line_start, line_end, buf)
        if error != None:
            errs_str = _("Error on line ") + str(i + 1) + ": " + error + _("\nLine contents: ") \
                       + buf.get_text(line_start, line_end, include_hidden_chars=False)
            return (None, errs_str)
        if av_tuple != None:
            arg_vals.append(av_tuple)
    
    return (arg_vals, None)

def _parse_line(line_start, line_end, buf):
    line = buf.get_text(line_start, line_end, include_hidden_chars=False)
    if len(line) == 0:
        return (None, None)
    if line.find("=") == -1:
        return (None, _("No \'=\' found."))
    sides = line.split("=")
    if len(sides) != 2:
        return (None, _("Number of tokens on line is ")+ str(len(sides)) + _(", should be 2 (key, value)."))
    k = sides[0].strip()
    v = sides[1].strip()
    if len(k) == 0:
        return (None, _("Arg name token is empty."))
    if len(v) == 0:
        return (None, _("Arg value token is empty."))
    try:
        k.decode('ascii')
    except UnicodeDecodeError:
        return (None, _("Non-ascii char in Arg name."))
    try:
        v.decode('ascii')
    except UnicodeDecodeError:
        return (None, _("Non-ascii char in Arg value."))
    if k.find(" ") != -1:
        return (None,  _("Whitespace in Arg name."))
    if v.find(" ") != -1:
        return (None,  _("Whitespace in Arg value."))
        
    return ((k,v), None)


class FileRenderPlayer(threading.Thread):
    
    def __init__(self, file_name, producer, consumer, start_frame, stop_frame):
        self.file_name = file_name
        self.producer = producer
        self.consumer = consumer
        self.start_frame = start_frame
        self.stop_frame = stop_frame

        threading.Thread.__init__(self)

    def run(self):
        self.running = True
        self.connect_and_start()
    
        while self.running: #set false at user site
            if self.producer.frame() > self.stop_frame:
                self.consumer.stop()
                self.producer.set_speed(0)
            time.sleep(0.1)

    def connect_and_start(self):
        self.consumer.purge()
        self.consumer.connect(self.producer)
        self.producer.set_speed(0)
        self.producer.seek(self.start_frame)
        self.consumer.start()
        self.producer.set_speed(1)
    
    def get_render_fraction(self):
        render_length = self.stop_frame - self.start_frame + 1
        if (self.producer.get_length() - 1) < 1:
            render_fraction = 1.0
        else:
            current_frame = self.producer.frame() - self.start_frame
            render_fraction = (float(current_frame)) / (float(render_length))
        if render_fraction > 1.0:
            render_fraction = 1.0
        return render_fraction


class ProgressWindowThread(threading.Thread):
    def __init__(self, dialog, progress_bar, clip_renderer, callback):
        self.dialog = dialog
        self.progress_bar = progress_bar
        self.clip_renderer = clip_renderer
        self.callback = callback
        threading.Thread.__init__(self)
    
    def run(self):        
        self.running = True
        
        while self.running:         
            render_fraction = self.clip_renderer.get_render_fraction()
            self.progress_bar.set_fraction(render_fraction)
            if self.clip_renderer.producer.get_speed() == 0:
                self.progress_bar.set_fraction(1.0)
                time.sleep(0.5)
                self.callback(self.dialog, 0)
                
            time.sleep(1)