# This file is an easy voxblox implentation based on taichi lang

import taichi as ti
import numpy as np
import matplotlib.pyplot as plt
import math
from matplotlib import cm
from .mapping_common import *

Wmax = 1000

@ti.data_oriented
class DenseESDF(Basemap):
    def __init__(self, map_scale=[10, 10], grid_scale=0.05, min_occupy_thres=0, texture_enabled=False, max_disp_particles=1000000, block_size=16):
        self.map_scale_xy = map_scale[0]
        self.map_scale_z = map_scale[1]

        self.block_size = block_size
        self.N = math.ceil(map_scale[0] / grid_scale/block_size)*block_size
        self.Nz = math.ceil(map_scale[1] / grid_scale/block_size)*block_size

        self.block_num_xy = math.ceil(map_scale[0] / grid_scale/block_size)
        self.block_num_z = math.ceil(map_scale[1] / grid_scale/block_size)

        self.grid_scale_xy = self.map_scale_xy/self.N
        self.grid_scale_z = self.map_scale_z/self.Nz
        
        self.max_disp_particles = max_disp_particles
        self.min_occupy_thres = min_occupy_thres

        self.TEXTURE_ENABLED = texture_enabled

        self.initialize_fields()

    def initialize_fields(self):
        self.num_export_particles = ti.field(dtype=ti.i32, shape=())
        self.num_export_TSDF_particles = ti.field(dtype=ti.i32, shape=())
        self.num_export_ESDF_particles = ti.field(dtype=ti.i32, shape=())
        self.input_R = ti.Matrix.field(3, 3, dtype=ti.f32, shape=())
        self.input_T = ti.Vector.field(3, dtype=ti.f32, shape=())
        self.export_x = ti.Vector.field(3, dtype=ti.f32, shape=self.max_disp_particles)
        self.export_color = ti.Vector.field(3, dtype=ti.i32, shape=self.max_disp_particles)
        self.export_TSDF = ti.field(dtype=ti.f32, shape=self.max_disp_particles)
        self.export_TSDF_xyz = ti.Vector.field(3, dtype=ti.f32, shape=self.max_disp_particles)
        self.export_ESDF = ti.field(dtype=ti.f32, shape=self.max_disp_particles)
        self.export_ESDF_xyz = ti.Vector.field(3, dtype=ti.f32, shape=self.max_disp_particles)

        self.grid_scale_ = ti.Vector([self.grid_scale_xy, self.grid_scale_xy, self.grid_scale_z])
        self.map_scale_ = ti.Vector([self.map_scale_xy, self.map_scale_xy, self.map_scale_z])
        self.NC_ = ti.Vector([self.N/2, self.N/2, self.Nz/2])
        self.N_ = ti.Vector([self.N, self.N, self.Nz])

        self.occupy = ti.field(dtype=int)
        self.TSDF = ti.field(dtype=ti.f32)
        self.W_TSDF = ti.field(dtype=ti.f32)
        self.ESDF = ti.field(dtype=ti.f32)
        self.color = ti.Vector.field(3, ti.i32)

        block_num_xy = self.block_num_xy
        block_num_z = self.block_num_z
        block_size = self.block_size

        B = ti.root.pointer(ti.ijk, (block_num_xy, block_num_xy, block_num_z))
        B = B.dense(ti.ijk, (block_size, block_size, block_size))
        B.place(self.occupy, self.W_TSDF,self.TSDF, self.ESDF)
        
        C = ti.root.pointer(ti.ijk, (block_num_xy, block_num_xy, block_num_z))
        C = C.dense(ti.ijk, (block_size, block_size, block_size))
        C.place(self.color)
        
        self.B = B
        self.C = C

    @ti.func
    def constrain_coor(self, _i):
        _i = _i.cast(int)
        for d in ti.static(range(3)):
            if _i[d] >= self.N_[d]:
                _i[d] = self.N_[d] - 1
            if _i[d] < 0:
                _i[d] = 0
        return _i

    @ti.kernel
    def recast_pcl_to_map(self, xyz_array: ti.ext_arr(), rgb_array: ti.ext_arr(), n: ti.i32):
        for index in range(n):
            pt = ti.Vector([
                xyz_array[index,0], 
                xyz_array[index,1], 
                xyz_array[index,2]])
            
            len_p2s = pt.norm()
            len_p2s_i = pt.norm()/self.grid_scale_xy

            #Vector from sensor to pointcloud
            pt_s2p = self.input_R@pt
            pt = pt_s2p + self.input_T

            pti = pt / self.grid_scale_ + self.NC_
            pti = self.constrain_coor(pti)
            self.occupy[pti] += 1
            #Easy recasting

            w_x_p = ti.static(1)
            j_f = 0.0
            for j in range(1, len_p2s_i):
                j_f += 1.0
                x_ = pt_s2p*j_f/len_p2s_i + self.input_T
                xi = x_ / self.grid_scale_ + self.NC_ #index of x
                xi = self.constrain_coor(xi)

                #vector from current voxel to point, e.g. p-x
                v2p = pt - x_
                d_x_p = v2p.norm()*self.grid_scale_xy
                d_x_p_s = d_x_p*sign(v2p.dot(pt_s2p))
                # print("T", self.input_T, "j", j, "x", x_, "pt_s2p",pt_s2p, "d_x_p_s", d_x_p_s)

                self.TSDF[xi] =  (self.TSDF[xi]*self.W_TSDF[xi]+w_x_p*d_x_p_s)/(self.W_TSDF[xi]+w_x_p)
                self.W_TSDF[xi] = ti.min(self.W_TSDF[xi]+w_x_p, Wmax)

            if ti.static(self.TEXTURE_ENABLED):
                for d in ti.static(range(3)):
                    self.color[pti][d] = rgb_array[index, d]
    
    @ti.kernel
    def get_occupy_to_particles(self, level: ti.template()):
        # Number for level
        self.num_export_particles[None] = 0
        for i, j, k in self.occupy:
            if self.occupy[i, j, k] > self.min_occupy_thres:
                index = ti.atomic_add(self.num_export_particles[None], 1)
                if self.num_export_particles[None] < self.max_disp_particles:
                    for d in ti.static(range(3)):
                        self.export_x[index][d] = ti.static([i, j, k][d])*self.grid_scale_[d] - self.map_scale_[d]/2
                        if ti.static(self.TEXTURE_ENABLED):
                            self.export_color[index] = self.color[i, j, k]
                else:
                    return

    @ti.kernel
    def get_TSDF_to_particles(self, level: ti.template()):
        # Number for TSDF
        self.num_export_TSDF_particles[None] = 0
        for i, j, k in self.TSDF:
            _index = (level+self.map_scale_[2]/2)/self.grid_scale_z
            if _index - 1 < k < _index + 1:
                index = ti.atomic_add(self.num_export_TSDF_particles[None], 1)
                if self.num_export_TSDF_particles[None] < self.max_disp_particles:
                    self.export_TSDF[index] = self.TSDF[i, j, k]
                    for d in ti.static(range(3)):
                        self.export_TSDF_xyz[index][d] = ti.static([i, j, k][d])*self.grid_scale_[d] - self.map_scale_[d]/2
                else:
                    return

    @ti.kernel
    def get_ESDF_to_particles(self, level: ti.template()):
        # Number for ESDF
        self.num_export_ESDF_particles[None] = 0
        for i, j, k in self.ESDF:
            index = ti.atomic_add(self.num_export_ESDF_particles[None], 1)
            if self.num_export_ESDF_particles[None] < self.max_disp_particles:
                self.export_ESDF[index] = self.ESDF[i, j, k]
                for d in ti.static(range(3)):
                    self.export_ESDF_xyz[index][d] = ti.static([i, j, k][d])*self.grid_scale_[d] - self.map_scale_[d]/2
            else:
                return

    def get_output_particles_occupy(self):
        return self.export_x.to_numpy(), self.export_color.to_numpy()
    
    def get_output_particles_TSDF(self):
        return self.export_TSDF_xyz.to_numpy(), self.export_TSDF.to_numpy()
    
    def get_output_particles_ESDF(self):
        return self.export_ESDF_xyz.to_numpy(), self.export_ESDF.to_numpy(),

    def render_sdf_to_particles(self, pars, pos_, sdf, num_particles_, grid_scale):
        if num_particles_ == 0:
            return

        max_sdf = np.max(sdf[0:num_particles_])
        min_sdf = np.min(sdf[0:num_particles_])
        colors = cm.gist_rainbow((sdf[0:num_particles_] - min_sdf)/(max_sdf-min_sdf))
        
        pos = pos_[0:num_particles_,:]
        pars.set_particles(pos)
        radius = np.ones(num_particles_)*grid_scale/2
        pars.set_particle_radii(radius)
        pars.set_particle_colors(colors)

    def handle_render(self, scene, gui, pars1, level, substeps = 3, pars_sdf=None):
        for e in gui.get_events(ti.GUI.PRESS):
            if e.key in [ti.GUI.ESCAPE, ti.GUI.EXIT]:
                exit()
            elif e.key == "-":
                level -= 0.5
            elif e.key == "=":
                level += 0.5

        self.get_occupy_to_particles(level)
        self.get_TSDF_to_particles(level)
        pos_, color_ = self.get_output_particles_occupy()
        self.render_occupy_map_to_particles(pars1, pos_, color_/255.0, self.num_export_particles[None], self.grid_scale_xy)
        tsdf_pos, tsdf = self.get_output_particles_TSDF()
        self.render_sdf_to_particles(pars_sdf, tsdf_pos, tsdf, self.num_export_TSDF_particles[None], self.grid_scale_xy)

        for i in range(substeps):
            scene.input(gui)
            scene.render()
            gui.set_image(scene.img)
            gui.text(content=f'Level {level:.2f} num_particles {self.num_export_particles[None]} grid_scale {self.grid_scale_xy} incress =; decress -',
                    pos=(0, 0.8),
                    font_size=20,
                    color=(0x0808FF))

            gui.show()
        return level, tsdf_pos