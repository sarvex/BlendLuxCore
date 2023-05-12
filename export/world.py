from ..bin import pyluxcore
from .. import utils
from ..nodes.output import get_active_output
from . import light
from ..utils.errorlog import LuxCoreErrorLog

# TODO: currently it is not possible to remove the world volume during viewport render


def convert(exporter, depsgraph, scene, is_viewport_render):
    props = pyluxcore.Properties()
    world = scene.world

    if not world:
        return props

    if world_light_props := light.convert_world(
        exporter, world, scene, is_viewport_render
    ):
        props.Set(world_light_props)

    if volume_node_tree := world.luxcore.volume:
        luxcore_name = utils.get_luxcore_name(volume_node_tree)
        active_output = get_active_output(volume_node_tree)
        try:
            active_output.export(exporter, depsgraph, props, luxcore_name)
            props.Set(pyluxcore.Property("scene.world.volume.default", luxcore_name))
        except Exception as error:
            msg = f'World "{world.name}": {error}'
            LuxCoreErrorLog.add_warning(msg)

    return props
