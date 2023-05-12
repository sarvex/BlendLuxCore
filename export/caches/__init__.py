import bpy
from ... import utils
from ...utils import EXPORTABLE_OBJECTS
from .. import camera, material

from .object_cache import ObjectCache2, supports_live_transform


class StringCache:
    def __init__(self):
        self.props = None

    def diff(self, new_props):
        props_str = str(self.props)
        new_props_str = str(new_props)

        if self.props is None:
            # Not initialized yet
            self.props = new_props
            return True

        self.props = new_props
        return props_str != new_props_str


class CameraCache:
    def __init__(self):
        self.string_cache = StringCache()

    @property
    def props(self):
        return self.string_cache.props

    def diff(self, exporter, scene, depsgraph, context):
        # String cache
        camera_props = camera.convert(exporter, scene, depsgraph, context)
        return self.string_cache.diff(camera_props)


class MaterialCache:
    def __init__(self):
        self.changed_materials = set()

    def diff(self, depsgraph):
        if depsgraph.id_type_updated("MATERIAL"):
            for dg_update in depsgraph.updates:
                if isinstance(dg_update.id, bpy.types.Material):
                    self.changed_materials.add(dg_update.id)
                    print("mat update:", dg_update.id.name)
        return self.changed_materials

    def update(self, exporter, depsgraph, is_viewport_render, props):
        for mat in self.changed_materials:
            lux_mat_name, mat_props = material.convert(exporter, depsgraph, mat, is_viewport_render)
            props.Set(mat_props)
        self.changed_materials.clear()


class VisibilityCache:
    def __init__(self):
        # sets containing keys
        self.last_visible_objects = None
        self.objects_to_remove = None
        
        self.has_new_objects = False

    def init(self, depsgraph, context):
        self.last_visible_objects = self._get_visible_objects(depsgraph, context)

    def diff(self, depsgraph, context):
        visible_objs = self._get_visible_objects(depsgraph, context)
        self.objects_to_remove = self.last_visible_objects - visible_objs
        self.has_new_objects = bool(visible_objs - self.last_visible_objects)
        self.last_visible_objects = visible_objs
        return bool(self.objects_to_remove) or self.has_new_objects

    def _get_visible_objects(self, depsgraph, context):
        keys = set()

        for dg_obj_instance in depsgraph.object_instances:
            if not supports_live_transform(dg_obj_instance.particle_system):
                continue

            if dg_obj_instance.show_self:
                # For duplis, check visibility of parent (emitter)
                obj = dg_obj_instance.parent if dg_obj_instance.parent else dg_obj_instance.object
                if obj.luxcore.exclude_from_render or not obj.visible_in_viewport_get(context.space_data):
                    continue
                keys.add(utils.make_key_from_instance(dg_obj_instance))
        return keys


class WorldCache:
    def __init__(self):
        self.world_name = None

    def diff(self, depsgraph):
        world = depsgraph.scene_eval.world
        world_updated = False

        if world:
            # TODO 2.8 for some reason this fires when editing a node tree, even when the world is not touched at all
            world_updated = depsgraph.id_type_updated("WORLD") or self.world_name != world.name_full

            # The sun influcences the world, e.g. through direction and turbidity if sky2 is used
            if world.luxcore.light == "sky2" and depsgraph.id_type_updated("OBJECT"):
                for dg_update in depsgraph.updates:
                    if dg_update.id == world.luxcore.sun:
                        world_updated = True
                        break
        elif self.world_name:
            # We had a world, but it was deleted
            world_updated = True

        self.world_name = world.name_full if world else None
        return world_updated
