from detectron2.utils.logger import setup_logger

from pointcloud import GraspCandidate
setup_logger()

import numpy as np
import cv2
import time

from detectron2 import model_zoo
from detectron2.engine import DefaultPredictor
from detectron2.config import get_cfg
from detectron2.utils.video_visualizer import VideoVisualizer
from detectron2.utils.visualizer import Visualizer
from detectron2.data import MetadataCatalog
import torch

from realsense import RSCamera
import pyrealsense2 as rs
import zmq
import utils
from logger import Logger
from frame_transformations import get_transformation_matrix_EulerXYZ, get_transformation_matrix_about_arb_axis, transform_frame_EulerXYZ
from pointcloud import GraspCandidate
import detection_msg_pb2
from streamer_receiver import VideoReceiver


SHOW_WINDOW_VIS = True
SEND_OUTPUT = True
SIMPLE_LOC = False
TARGET_OBJECT = 'bottle'

cam = utils.RSCameraMockup()
grasp = GraspCandidate()
receiver = VideoReceiver()

output = cv2.VideoWriter(utils.VIDEO_FILE, cv2.VideoWriter_fourcc(
            'M', 'J', 'P', 'G'), 10, (cam.width, cam.height))

output_depth = cv2.VideoWriter(utils.VIDEO_DEPTH_FILE, cv2.VideoWriter_fourcc(
    'M', 'J', 'P', 'G'), 10, (cam.width, cam.height))

output_raw = cv2.VideoWriter(utils.VIDEO_RAW_FILE, cv2.VideoWriter_fourcc(
    'M', 'J', 'P', 'G'), 10, (cam.width, cam.height))

output_grasp = cv2.VideoWriter(utils.VIDEO_GRASP_FILE, cv2.VideoWriter_fourcc(
    'M', 'J', 'P', 'G'), 10, (cam.width, cam.height))

logger = Logger()
records = np.empty((0, logger.cols))



cfg = get_cfg()
cfg.merge_from_file(model_zoo.get_config_file('COCO-InstanceSegmentation/mask_rcnn_R_50_FPN_3x.yaml'))
cfg.MODEL.ROI_HEADS.SCORE_THRESH_TEST = 0.5
cfg.MODEL.WEIGHTS = model_zoo.get_checkpoint_url('COCO-InstanceSegmentation/mask_rcnn_R_50_FPN_3x.yaml')
predictor = DefaultPredictor(cfg)
metadata = MetadataCatalog.get(cfg.DATASETS.TRAIN[0])
v = VideoVisualizer(metadata)
class_catalog = metadata.thing_classes

context = zmq.Context()
socket = context.socket(zmq.REP)

if SEND_OUTPUT:
    socket.connect('tcp://localhost:2222')

starting_time = time.time()
frame_counter = 0
elapsed_time = 0
serial_msg = None
quad_pose = None
previous_quad_pose = None

while True:
    try:
        if SEND_OUTPUT:
            quad_pose_serial = socket.recv()
            quad_pose = detection_msg_pb2.Detection()
            quad_pose.ParseFromString(quad_pose_serial)
            # print(f'received pose after {(time.time() - pose_time)*1000}')

            


            # print(quad_pose)
        serial_msg = None
        frame, depth_frame = receiver.recv_frames()
        # frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        frame = torch.from_numpy(frame)
        cam_intrinsics = cam.intrinsics
        
        # depth_colormap = cam.colorize_frame(depth_frame)

        # frame = np.asarray(frame.get_data())
        # depth_frame = np.asanyarray(depth_frame.get_data())

        # output_depth.write(depth_colormap)
        # output_depth.write(depth_frame)
        # output_raw.write(frame)

        frame, depth_frame = receiver.recv_frames()
     
        outputs = predictor(frame)
        detected_class_idxs = outputs['instances'].pred_classes
        pred_boxes = outputs['instances'].pred_boxes
        
        out = v.draw_instance_predictions(frame, outputs['instances'].to('cpu'))

        # v = Visualizer(frame[:, :, ::-1], metadata=metadata, scale=1.2)
        # out = v.draw_instance_predictions(outputs['instances'].to('cpu'))
        
        # vis_frame = np.asarray(out.get_image()[:, :, ::-1])
        vis_frame = np.asarray(out.get_image())

        mask_array = outputs['instances'].pred_masks.to('cpu').numpy()
        num_instances = mask_array.shape[0]

        mask_array = np.moveaxis(mask_array, 0, -1)
        mask_array_instance = []
        
        # Loop over instances that have been detected
        for i in range(num_instances):

            class_idx = detected_class_idxs[i]
            bbox = pred_boxes[i].to('cpu')
            class_name = class_catalog[class_idx]
            [(xmin, ymin, xmax, ymax)] = bbox
            # cv2.rectangle(frame, (int(xmin), int(ymin)), (int(xmax), int(ymax)), (255, 0, 0), 2)
            xmin = int(xmin)
            ymin = int(ymin)
            xmax = int(xmax)
            ymax = int(ymax)

            center_x = (xmax - xmin)//2 + xmin
            center_y = (ymax - ymin)//2 + ymin

            if center_y < cam.height and center_x < cam.width:
                        depth = depth_frame[center_y,
                            center_x].astype(float)
                        distance = depth * cam.depth_scale
            else:
                # No valid distance found
                distance = 0.0
                print('invalid coordinates')


            # Get translation vector relative to the camera frame
            tvec = cam.deproject(cam_intrinsics, center_x, center_y, distance)
            easy_center = tvec
            # print(f'{class_name} at {tvec}')
            # Realsense y-axis points down by default
            tvec[1] = -tvec[1]
            yaw = 0

            mask_array_instance.append(mask_array[:, :, i:(i+1)])
            obj_mask = np.zeros_like(frame)
            obj_mask = np.where(mask_array_instance[i] == True, 255, obj_mask)
            # cv2.imwrite(f'pictures/mask_{class_name}.png',obj_mask)

            
            if class_name == TARGET_OBJECT:
    
                if SEND_OUTPUT and SIMPLE_LOC:
                    print('Object location (simple localization) -----')
                    print(tvec)
                    # cam_2_drone_translation = [0.1267, 0, -0.0416]
                    cam_2_drone_translation = [0.1267, -0.01, 0.0]

                    cam_2_drone_orientation = [0, -30, 0]

                    translation = [
                        quad_pose.x, quad_pose.y, quad_pose.z]
                    rotation = [
                        quad_pose.roll, -quad_pose.pitch, quad_pose.yaw]

                    # print('Quad translation: -----')
                    # print(translation)
                    # print('Quad rotation: ----')
                    # print(rotation)

                    tvec = [tvec[2], tvec[0], tvec[1], 1]

                    
                    # Transform into drone frame
                    tvec = transform_frame_EulerXYZ(cam_2_drone_orientation, cam_2_drone_translation, tvec, degrees=True)
                    # print(f"Transform to drone frame: {tvec}")
                    # Transform into mocap frame
                    tvec = transform_frame_EulerXYZ(
                        rotation, translation, tvec, degrees=False)
                    print(f'Transform to mocap frame: {tvec}')
                    # print(tvec)
                    
                    

                msg = detection_msg_pb2.Detection()

                
                # Create point cloud of detected object
                masked_frame = cv2.bitwise_and(frame, obj_mask)
                # cv2.imwrite('masked_frame.png', masked_frame)
                if RECORD_PCD_DATA:
                    grasp_color = GraspCandidate()
                    grasp_masked = GraspCandidate()
                    
                    grasp_masked.set_point_cloud_from_aligned_masked_frames(masked_frame, depth_frame, cam_intrinsics)
                    grasp_color.set_point_cloud_from_aligned_frames(frame, depth_frame, cam_intrinsics)
                    
                    grasp_masked.save_pcd(f'pcd/pcd_logs/{utils.RECORD_COUNTER}_{TARGET_OBJECT}_masked_{frame_counter}.pcd')
                    grasp_color.save_pcd(f'pcd/pcd_logs/{utils.RECORD_COUNTER}_{TARGET_OBJECT}_full_{frame_counter}.pcd')
                    
                    print(f'Recorded pcd for frame {frame_counter}, sleeping briefly')
                    time.sleep(3)
                

                cam_2_drone_translation = [0.1267, -0.01, -0.09]

                cam_2_drone_orientation = [0, -30, 0]

                translation = [
                    quad_pose.x, quad_pose.y, quad_pose.z]
                rotation = [
                    quad_pose.roll, -quad_pose.pitch, quad_pose.yaw]

                # print('Quad translation: -----')
                # print(translation)
                # print('Quad rotation: ----')
                # print(rotation)

                T_cam_2_drone = get_transformation_matrix_EulerXYZ(cam_2_drone_orientation. cam_2_drone_translation, degrees=True)
                T_drone_2_global = get_transformation_matrix_EulerXYZ(rotation, translation, degrees=False)

                T_total = np.matmul(T_drone_2_global, T_cam_2_drone)
                
                grasp_points = None
                try:
                    grasp.add_point_cloud_from_aligned_masked_frames(masked_frame, depth_frame, cam_intrinsics, T_total)
                    centroid = grasp.find_centroid()
                    # axis_ext, _, _ = grasp.find_largest_axis()
                    # axis = axis_ext[0]
                    # pcd = grasp.rotate_pcd_around_axis(grasp.pointcloud, centroid, math.pi, axis)
                    # grasp.pointcloud += pcd
                    grasp.save_pcd(f'pcd/pointcloud_{TARGET_OBJECT}_{utils.RECORD_COUNTER}.pcd')
                    grasp_points = grasp.find_grasping_points()
                    # print('found grasping points')
                except Exception as e:
                    print('pcd data analysis went wrong')
                    print(e)
                # # print(f'Grasp points: {grasp_points}')
                # # print(f'Translation: {tvec}')
                if grasp_points is not None:
                    # Get points in pixels (project back into image from 3D point)
                    p1 = np.asanyarray(cam.project(cam_intrinsics, grasp_points[0]))
                    p2 = np.asanyarray(cam.project(cam_intrinsics, grasp_points[1]))

                    img = cv2.circle(frame, (int(p1[0]), int(p1[1])), 3, (0,255,0))
                    img = cv2.circle(frame, (int(p2[0]), int(p2[1])), 3, (0,255,0))
                    output_grasp.write(img)
                    delta_x = p1[0] - p2[0]
                    delta_y = np.abs(p1[1] - p2[1])

                    yaw = np.abs(np.arctan(delta_x/delta_y) * 180/np.pi - 90)
                
             
                if SEND_OUTPUT and not SIMPLE_LOC:
                    tvec = [-centroid[0], centroid[1], -centroid[2], 1]
                  
                    


                    grasp.add_point_cloud_from_aligned_masked_frames()


                    tvec = [tvec[2], tvec[0], tvec[1], 1]
                    print(f'Mocap axis tvec: {tvec}')

                    
                    # Transform into drone frame
                    tvec = transform_frame_EulerXYZ(cam_2_drone_orientation, cam_2_drone_translation, tvec, degrees=True)
                    print(f"Transform to drone frame: {tvec}")
                    # Transform into mocap frame
                    tvec = transform_frame_EulerXYZ(
                        rotation, translation, tvec, degrees=False)

                    # print(f'Transform to mocap frame: {tvec}')

               
                    
                
                
                if msg is not None and serial_msg is not None:
                    logger.record_value([np.array(
                            [tvec[0], tvec[1], tvec[2], elapsed_time, 0, class_name]), ])
                    print(f'logged {tvec}')
                # x_mean = np.mean(logger.records[:, 0].astype(float))
                # y_mean = np.mean(logger.records[:, 1].astype(float))
                # z_mean = np.mean(logger.records[:, 2].astype(float))
                # msg.x = x_mean
                # msg.y = y_mean
                # msg.z = z_mean
                msg.x = tvec[0]
                msg.y = tvec[1]
                msg.z = tvec[2]
                msg.yaw = yaw
                msg.label = class_name
                msg.confidence = 0
                serial_msg = msg.SerializeToString()

                # Log values
                elapsed_time = time.time() - starting_time
                
        if SHOW_WINDOW_VIS:
            cv2.imshow('output', vis_frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
        
        if SEND_OUTPUT:
            previous_quad_pose = quad_pose

            if serial_msg is not None:
                socket.send(serial_msg)
            else:
                msg = detection_msg_pb2.Detection()
                msg.x = 0.0
                msg.y = 0.0
                msg.z = 0.0
                msg.label = 'Nothing'
                msg.confidence = 0.0
                serial_msg = msg.SerializeToString()
                socket.send(serial_msg)
        
        output.write(vis_frame)
        frame_counter += 1


    except KeyboardInterrupt as e:
        output.release()
        output_depth.release()
        output_raw.release()
        output_grasp.release()
        cam.release()
        receiver.image_hub.close()
        cv2.destroyAllWindows()
        socket.close()
        logger.export_to_csv(utils.LOG_FILE)

