import bpy
from bpy.props import EnumProperty, FloatProperty
from ..base import LuxCoreNodeTexture

from .. import NOISE_BASIS_ITEMS, MIN_NOISE_SIZE
from ...utils import node as utils_node


class LuxCoreNodeTexBlenderDistortedNoise(LuxCoreNodeTexture, bpy.types.Node):
    bl_label = "Blender Distorted Noise"
    bl_width_default = 200

    noise_basis: EnumProperty(update=utils_node.force_viewport_update, name="Noise Basis", description="Type of noise used", items=NOISE_BASIS_ITEMS,
                                        default="blender_original")
    noise_type: EnumProperty(update=utils_node.force_viewport_update, name="Type", description="Type of noise used", items=NOISE_BASIS_ITEMS,
                                  default="blender_original")
    dist_amount: FloatProperty(update=utils_node.force_viewport_update, name="Distortion", default=1.00)
    noise_size: FloatProperty(update=utils_node.force_viewport_update, name="Noise Size", default=0.25, min=MIN_NOISE_SIZE)
    bright: FloatProperty(update=utils_node.force_viewport_update, name="Brightness", default=1.0, min=0)
    contrast: FloatProperty(update=utils_node.force_viewport_update, name="Contrast", default=1.0, min=0)

    def init(self, context):
        self.add_input("LuxCoreSocketMapping3D", "3D Mapping")
        self.outputs.new("LuxCoreSocketColor", "Color")

    def draw_buttons(self, context, layout):
        layout.prop(self, "noise_basis")
        layout.prop(self, "noise_type")

        col = layout.column(align=True)
        col.prop(self, "noise_size")
        col.prop(self, "dist_amount")

        column = layout.column(align=True)
        column.prop(self, "bright")
        column.prop(self, "contrast")

    def sub_export(self, exporter, depsgraph, props, luxcore_name=None, output_socket=None):
        definitions = {
            "type": "blender_distortednoise",
            "noise_distortion": self.noise_type,
            "noisebasis": self.noise_basis,            
            "noisesize": self.noise_size,
            "distortion": self.dist_amount,
            "bright": self.bright,
            "contrast": self.contrast,
        }
        definitions |= self.inputs["3D Mapping"].export(exporter, depsgraph, props)
        return self.create_props(props, definitions, luxcore_name)
