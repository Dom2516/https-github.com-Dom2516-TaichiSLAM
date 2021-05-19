from taichi_slam.mapping import *
from taichi_slam.utils.visualization import *
from taichi_slam.utils.ros_pcl_transfer import *
import numpy as np
import rospy
import sensor_msgs.point_cloud2 as pc2
from geometry_msgs.msg import TransformStamped
from sensor_msgs.msg import PointCloud2, PointCloud
import ros_numpy
import tina

cur_trans = None
pub = None
project_in_taichi = True
disp_in_rviz = False

def taichioctomap_pcl_callback(octomap, cur_trans, msg):
    if cur_trans is None:
        return
    if octomap.TEXTURE_ENABLED:
        xyz_array, rgb_array = pointcloud2_to_xyz_rgb_array(msg)
    else:
        xyz_array = ros_numpy.point_cloud2.pointcloud2_to_xyz_array(msg)
        rgb_array = np.array([], dtype=int)

    _R, _T = transform_msg_to_numpy(cur_trans)

    for i in range(3):
        octomap.input_T[None][i] = _T[i]
        for j in range(3):
            octomap.input_R[None][i, j] = _R[i, j]
    octomap.recast_pcl_to_map(xyz_array, rgb_array, len(xyz_array))

    global level
    if disp_in_rviz:
        pub_to_ros(pub, octomap.x.to_numpy(), octomap.color.to_numpy(), octomap.TEXTURE_ENABLED)
    level, pos_ = octomap.handle_render(scene, gui, pars1, level, pars_sdf=pars2)

def pub_to_ros(pub, pos_, colors_, TEXTURE_ENABLED):
    if TEXTURE_ENABLED:
        pts = np.concatenate((pos_, colors_.astype(float)/255.0), axis=1)
        pub.publish(point_cloud(pts, '/world', has_rgb=True))
    else:
        pub.publish(point_cloud(pos_, '/world'))


def pose_call_back(msg):
    global cur_trans
    cur_trans = msg

def ros_subscribe_pcl():
    sub2 = rospy.Subscriber("/kinect/vrpn_client/estimated_transform", TransformStamped, pose_call_back)
    sub1 = rospy.Subscriber("/camera/depth_registered/points", PointCloud2, pcl_callback)

    r = rospy.Rate(10) # 10hz 
    while not rospy.is_shutdown():
        try:
            r.sleep()
        except KeyboardInterrupt:
            break


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Taichi slam fast demo')
    parser.add_argument("-r","--resolution", nargs=2, type=int, help="display resolution", default=[1024, 768])
    parser.add_argument("-m","--method", type=str, help="dense mapping method", default="octomap")
    parser.add_argument("-c","--cuda", help="display resolution", action='store_true')
    parser.add_argument("-t","--texture-enabled", help="showing the point cloud's texture", action='store_true')
    parser.add_argument("--rviz", help="output to rviz", action='store_true')
    parser.add_argument("-p", "--max-disp-particles", help="max output voxels", type=int,default=1000000)
    parser.add_argument("-b","--bagpath", help="path of bag", type=str,default='')
    parser.add_argument("-o","--occupy-thres", help="thresold for occupy", type=int,default=2)
    parser.add_argument("-s","--map-scale", help="scale of map xy,z", nargs=2, type=float, default=[100, 10])
    parser.add_argument("-g","--grid-scale", help="scale of grid", type=float, default=0.05)
    parser.add_argument("-K", help="division each axis of octomap, when K>2, octo will be K**3 tree", type=int, default=2)

    args = parser.parse_args()

    RES_X = args.resolution[0]
    RES_Y = args.resolution[1]
    disp_in_rviz = args.rviz
    
    print()
    print(f"Res [{RES_X}x{RES_Y}] GPU {args.cuda} RVIZ {disp_in_rviz} scale of map {args.map_scale} grid {args.grid_scale} ")
    if args.cuda:
        ti.init(arch=ti.cuda)
    else:
        ti.init(arch=ti.cpu, debug=True)


    gui = ti.GUI('TaichiOctomap', (RES_X, RES_Y))
    level = 1
    scene = tina.Scene(RES_X, RES_Y, bgcolor=(0.1, 0.1, 0.1))
    pars1 = tina.SimpleParticles(maxpars=args.max_disp_particles)
    pars2 = tina.SimpleParticles(maxpars=args.max_disp_particles)
    material1 = tina.Lamp()
    material2 = tina.Lamp()
    scene.add_object(pars1, material1)
    scene.add_object(pars2, material2)
    if args.method == "octomap":
        mapping = Octomap(texture_enabled=args.texture_enabled, 
            max_disp_particles=args.max_disp_particles, 
            min_occupy_thres = args.occupy_thres,
            map_scale=args.map_scale,
            grid_scale=args.grid_scale,
            K=args.K)
    elif args.method == "esdf":
        mapping = DenseESDF(texture_enabled=args.texture_enabled, 
            max_disp_particles=args.max_disp_particles, 
            min_occupy_thres = args.occupy_thres,
            map_scale=args.map_scale,
            grid_scale=args.grid_scale)

    scene.init_control(gui, radius=10, theta=-1.57,center=(0, 0, 0), is_ortho=True)
    
    if disp_in_rviz:
        rospy.init_node("TaichiOctomap", disable_signals=False)
        pub = rospy.Publisher('/pcl', PointCloud2, queue_size=10)

    if args.bagpath == "":
        print("No data input, using random generate maps")
        mapping.random_init_octo(1000)
        while gui.running:
            level, _ = mapping.handle_render(mapping, scene, gui, pars, level)
    else:
        iteration_over_bag(args.bagpath, 
            lambda _1, _2: taichioctomap_pcl_callback(mapping, _1, _2))

