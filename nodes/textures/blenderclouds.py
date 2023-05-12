import bpy
from bpy.props import EnumProperty, FloatProperty, IntProperty
from ..base import LuxCoreNodeTexture

from .. import NOISE_BASIS_ITEMS, NOISE_TYPE_ITEMS, MIN_NOISE_SIZE
from ...utils import node as utils_node


class LuxCoreNodeTexBlenderClouds(LuxCoreNodeTexture, bpy.types.Node):
    bl_label = "Blender Clouds"
    bl_width_default = 200

    noise_type: EnumProperty(update=utils_node.force_viewport_update, name="Noise Type", description="Soft or hard noise", items=NOISE_TYPE_ITEMS,
                                       default="soft_noise")
    noise_basis: EnumProperty(update=utils_node.force_viewport_update, name="Basis", description="Basis of noise used", items=NOISE_BASIS_ITEMS,
                                        default="blender_original")
    noise_size: FloatProperty(update=utils_node.force_viewport_update, name="Noise Size", default=0.25, min=MIN_NOISE_SIZE)
    noise_depth: IntProperty(update=utils_node.force_viewport_update, name="Noise Depth", default=2, min=0, soft_max=10, max=25)
    bright: FloatProperty(update=utils_node.force_viewport_update, name="Brightness", default=1.0, min=0)
    contrast: FloatProperty(update=utils_node.force_viewport_update, name="Contrast", default=1.0, min=0)

    def init(self, context):
        self.add_input("LuxCoreSocketMapping3D", "3D Mapping")
        self.outputs.new("LuxCoreSocketColor", "Color")

    def draw_buttons(self, context, layout):
        layout.prop(self, "noise_type", expand=True)
        layout.prop(self, "noise_basis")

        col = layout.column(align=True)
        col.prop(self, "noise_size")
        col.prop(self, "noise_depth")
        
        column = layout.column(align=True)
        column.prop(self, "bright")
        column.prop(self, "contrast")

    def sub_export(self, exporter, depsgraph, props, luxcore_name=None, output_socket=None):
        definitions = {
            "type": "blender_clouds",
            "noisetype": self.noise_type,
            "noisebasis": self.noise_basis,
            "noisesize": self.noise_size,
            "noisedepth": self.noise_depth,
            "bright": self.bright,
            "contrast": self.contrast,
        }
        definitions |= self.inputs["3D Mapping"].export(exporter, depsgraph, props)
        return self.create_props(props, definitions, luxcore_name)
