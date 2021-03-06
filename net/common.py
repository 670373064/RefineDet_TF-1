import math
import numpy as np
import tensorflow as tf
from net import bboxes
from tf_extended import tensors


def conv_act_layer(from_layer, name, num_filter, use_bn=False, kernel=3, stride=1, pad='valid', data_format='channels_last', act_type="relu", num=1):
    """
    wrapper for a small Convolution group

    Parameters:
    ----------
    from_layer : mx.symbol
        continue on which layer
    name : str
        base name of the new layers
    num_filter : int
        how many filters to use in Convolution layer
    kernel : tuple (int, int)
        kernel size (h, w)
    pad : tuple (int, int)
        padding size (h, w)
    stride : tuple (int, int)
        stride size (h, w)
    act_type : str
        activation type, can be relu...
    use_batchnorm : bool
        whether to use batch normalization

    Returns:
    ----------
    (conv, relu)
    """
    conv = tf.layers.conv2d(from_layer, num_filter,
                            kernel, stride, pad, data_format, name="{}_conv{}".format(name, num))
    if use_bn:
        conv = tf.layers.batch_normalization(
            conv, name="{}_bn{}".format(name, num))
    if act_type:
        conv = tf.nn.relu(conv, name="{}_{}{}".format(name, act_type, num))
    return conv

def deconv_act_layer(from_layer, name, num_filter, use_bn=False, kernel=2, padding="same", strides=2, data_format='channels_last',  act_type='relu'):
    deconv = tf.layers.conv2d_transpose(inputs=from_layer, filters=num_filter, kernel_size=kernel, strides=strides,
                                        padding=padding, data_format=data_format, name="{}_deconv".format(name))
    if use_bn:
        deconv = tf.layers.batch_normalization(
            deconv, name="{}_bn".format(name))
    if act_type:
        deconv = tf.nn.relu(deconv, name="{}_{}".format(name, act_type))
    return deconv

def tcb_module(conv, deconv, num_filter, level=1):
    deconv = deconv_act_layer(deconv, "tcb{}".format(
        level), num_filter, use_bn=False, kernel=2, padding="same", strides=2, data_format='channels_last', act_type=None)
    conv1 = conv_act_layer(conv, "tcb{}".format(level), num_filter, use_bn=False, kernel=3,
                             stride=1, pad='same', act_type="relu", num=1)
    conv2 = conv_act_layer(conv1, "tcb{}".format(level), num_filter, use_bn=False, kernel=3,
                             stride=1, pad='same', act_type=None, num=2)
    eltwise_sum = tf.add(deconv, conv2)
    relu = tf.nn.relu(eltwise_sum, name="tcb{}_elt_relu".format(level))
    conv3 = conv_act_layer(relu, "tcb{}".format(level), num_filter, use_bn=False, kernel=3,
                             stride=1, pad='same', act_type="relu", num=3)
    return conv3

def tcb_module_last(conv, num_filter, level=1):
    conv1 = conv_act_layer(conv, "tcb{}".format(level), num_filter, use_bn=False, kernel=3,
                                 stride=1, pad='same', act_type="relu", num=1)
    conv2 = conv_act_layer(conv1, "tcb{}".format(level), num_filter, use_bn=False, kernel=3,
                                 stride=1, pad='same', act_type="relu", num=2)
    conv3 = conv_act_layer(conv2, "tcb{}".format(level), num_filter, use_bn=False, kernel=3,
                                 stride=1, pad='same', act_type="relu", num=3)
    return conv3

def construct_refinedet(from_layers):
    out_layers = []
    layers = from_layers[::-1]
    for k, layer in enumerate(layers):
        if k == 0:
            out_layer = tcb_module_last(layer, 256, level=4-k)
        else:
            out_layer = tcb_module(layer, out_layer, 256, level=4-k)
        out_layers.append(out_layer)
    return out_layers[::-1]

# ==============================================================================================================
#定义从ssd的一个层回去anchor 的方法  #ssd计算anchor 的方法
def ssd_anchor_one_layer(img_shape,
                         feat_shape, #featuremap size
                         sizes, #anchor size
                         ratios, #anchor ratio
                         step,  #anchor stride
                         offset=0.5,
                         dtype=np.float32):
    """Computer SSD default anchor boxes for one feature layer.

    Determine the relative position grid of the centers, and the relative
    width and height.

    Arguments:
      feat_shape: Feature shape, used for computing relative position grids; 特征大小用来计算为自豪网格
      size: Absolute reference sizes; #
      ratios: Ratios to use on these features;
      img_shape: Image shape, used for computing height, width relatively to the
        former;
      offset: Grid offset. 相对坐标

    Return:
      y, x, h, w: Relative x and y grids, and height and width.
        y,x: [feat_width, feat_height]
        h,w: [anchor_number]
    """
    # Compute the position grid: simple way.
    # y, x = np.mgrid[0:feat_shape[0], 0:feat_shape[1]]
    # y = (y.astype(dtype) + offset) / feat_shape[0]
    # x = (x.astype(dtype) + offset) / feat_shape[1]
    # Weird SSD-Caffe computation using steps values...
    y, x = np.mgrid[0:feat_shape[0], 0:feat_shape[1]]  #图像宽高，建立矩阵
    y = (y.astype(dtype) + offset) * step / img_shape[0] #计算特征图到图像岛地映射，归一化到0-1
    x = (x.astype(dtype) + offset) * step / img_shape[1]

    # Expand dims to support easy broadcasting.
    y = np.expand_dims(y, axis=-1)
    x = np.expand_dims(x, axis=-1)

    # Compute relative height and width.
    # Tries to follow the original implementation of SSD for the order.
    num_anchors = len(sizes) + len(ratios)  #anchor 数量时ratio和size的和
    h = np.zeros((num_anchors, ), dtype=dtype)
    w = np.zeros((num_anchors, ), dtype=dtype)
    # Add first anchor boxes with ratio=1.
    h[0] = sizes[0] / img_shape[0]  #添加1：1的default框
    w[0] = sizes[0] / img_shape[1]
    di = 1
    if len(sizes) > 1:  #size=2 #又加一个框
        h[1] = math.sqrt(sizes[0] * sizes[1]) / img_shape[0]
        w[1] = math.sqrt(sizes[0] * sizes[1]) / img_shape[1]
        di += 1
    for i, r in enumerate(ratios):#根据长宽比加anchor
        h[i+di] = sizes[0] / img_shape[0] / math.sqrt(r)
        w[i+di] = sizes[0] / img_shape[1] * math.sqrt(r)
    return x, y, w, h    #返回中心点和宽高坐标
#定义获取anchor 的方法，为所有的特征图featuremap计算anchorbox
def ssd_anchors_all_layers(img_shape,
                           layers_shape,
                           anchor_sizes,
                           anchor_ratios,
                           anchor_steps,
                           offset=0.5,
                           dtype=np.float32):
    """Compute anchor boxes for all feature layers.
    """
    layers_anchors = []
    for i, s in enumerate(layers_shape):
        #分别计算ssd中每一层的anchorbbox，然后append成list返回
        anchor_bboxes = ssd_anchor_one_layer(img_shape, s,
                                             anchor_sizes[i],
                                             anchor_ratios[i],
                                             anchor_steps[i],
                                             offset=offset, dtype=dtype)
        layers_anchors.append(anchor_bboxes)
    return layers_anchors
#给定image尺寸，计算default box
def get_anchors(config, from_layers, dtype=np.float32):
    """Compute the default anchor boxes, given an image shape.
    """
    image_shape = config['image_shape']
    anchor_sizes = config['anchor_sizes']
    anchor_ratios = config['anchor_ratios']
    anchor_steps = config['arm_anchor_steps']
    anchor_offset = config['anchor_offset']
    if anchor_steps:
        assert len(anchor_steps) == len(from_layers), \
         "provide steps for all layers or leave empty"
    feat_shapes = [from_layer.get_shape().as_list()[1:3] for from_layer in from_layers]
    return ssd_anchors_all_layers(image_shape, feat_shapes, anchor_sizes, anchor_ratios, 
                        anchor_steps, anchor_offset, dtype)
# =================================================================================================================

def getpred(config, from_layers, num_classes, sizes, ratios, mode='arm', clip=False,
            interm_layer_channel=0, steps=[], anchor_offset=0.5):
    loc_layers = []
    cls_layers = []
    anchor_layers = []
    num_classes += 1  # always use background as label 0
    for k, from_layer in enumerate(from_layers):
        from_name = from_layer.name
        # Add intermediate layers.
        if interm_layer_channel > 0:
            from_layer = tf.layers.conv2d(from_layer, interm_layer_channel, kernel_size=3, strides=1, 
                            padding="same", data_format='channels_last', name="{}_inter_conv".format(from_name).replace(':','_'))
            from_layer = tf.nn.relu(
                from_layer, name="{}_inter_relu".format(from_name).replace(':','_'))

        # estimate number of anchors per location
        size = sizes[k]
        assert len(size) > 0, "must provide at least one size"
        ratio = ratios[k]
        assert len(ratio) > 0, "must provide at least one ratio"
        num_anchors = len(size) + len(ratio)
        
        # create location prediction layer
        num_loc_pred = num_anchors * 4
        loc_pred = tf.layers.conv2d(from_layer, num_loc_pred, kernel_size=3, strides=1,
                 padding="same", data_format='channels_last', name="{}_loc_conv".format(from_name).replace(':','_'))
        loc_shape = loc_pred.get_shape().as_list()
        loc_pred = tf.reshape(loc_pred,[-1]+loc_shape[1:3]+[num_anchors,4])
        # loc_pred = tf.transpose(loc_pred, perm=(0, 2, 3, 1))
        loc_layers.append(loc_pred)

        # create class prediction layer
        num_cls_pred = num_anchors * num_classes
        cls_pred = tf.layers.conv2d(from_layer, num_cls_pred, kernel_size=3, strides=1,
                 padding="same", data_format='channels_last', name="{}_cls_conv".format(from_name).replace(':','_'))
        # cls_pred = tf.transpose(cls_pred, perm=(0, 2, 3, 1))
        # cls_pred = tf.layers.flatten(cls_pred)
        cls_pred = tf.nn.softmax(cls_pred)
        cls_shape = cls_pred.get_shape().as_list()
        cls_pred = tf.reshape(cls_pred, [-1]+cls_shape[1:3]+[num_anchors,num_classes])
        cls_layers.append(cls_pred)
    
    return loc_layers, cls_layers

def multi_layer_feature(body, from_layers):
    """Wrapper function to extract features from base network, attaching extra
    layers and SSD specific layers

    Parameters
    ----------
    from_layers : list of str
        feature extraction layers, use '' for add extra layers
        For example:
        from_layers = ['relu4_3', 'fc7', '', '', '', '']
        which means extract feature from relu4_3 and fc7, adding 4 extra layers
        on top of fc7
    num_filters : list of int
        number of filters for extra layers, you can use -1 for extracted features,
        however, if normalization and scale is applied, the number of filter for
        that layer must be provided.
        For example:
        num_filters = [512, -1, 512, 256, 256, 256]
    strides : list of int
        strides for the 3x3 convolution appended, -1 can be used for extracted
        feature layers
    pads : list of int
        paddings for the 3x3 convolution, -1 can be used for extracted layers
    min_filter : int
        minimum number of filters used in 1x1 convolution

    Returns
    -------
    list of convolution neuraul network
    """
    # arguments check
    assert len(from_layers) > 0
    assert isinstance(from_layers[0], str) and len(from_layers[0].strip()) > 0
    internals = body.get_internals()
    layers = []
    for k, from_layer in enumerate(from_layers):
        if from_layer.strip():
            # extract from base network
            layer = internals[from_layer.strip() + '_output']
            layers.append(layer)
        else:
            # attach from last feature layer
            assert len(layers) > 0
            layer = layers[-1]
            conv6_1 = conv_act_layer(layer, 'conv6_1', 256, use_bn=False, kernel=[
                                     1, 1, 1], stride=[1, 1, 1], pad='valid', data_format='channels_last', act_type='relu', num=1)
            conv6_2 = conv_act_layer(conv6_1, 'conv6_2', 512, use_bn=False, kernel=[
                                     3, 3, 3], stride=[2, 2, 2], pad='valid',  data_format='channels_last', act_type='relu', num=1)
            layers.append(conv6_2)
    return layers

def multibox_layer(config, layers, num_classes, clip=False):
    """
    the basic aggregation module for SSD detection. Takes in multiple layers,
    generate multiple object detection targets by customized layers

    Parameters:
    ----------
    from_layers : list of mx.symbol
        generate multibox detection from layers
    num_classes : int
        number of classes excluding background, will automatically handle
        background in this function
    sizes : list or list of list
        [min_size, max_size] for all layers or [[], [], []...] for specific layers
    ratios : list or list of list
        [ratio1, ratio2...] for all layers or [[], [], ...] for specific layers
    normalizations : int or list of int
        use normalizations value for all layers or [...] for specific layers,
        -1 indicate no normalizations and scales
    num_channels : list of int
        number of input layer channels, used when normalization is enabled, the
        length of list should equals to number of normalization layers
    clip : bool
        whether to clip out-of-image boxes
    interm_layer : int
        if > 0, will add a intermediate Convolution layer
    steps : list
        specify steps for each MultiBoxPrior layer, leave empty, it will calculate
        according to layer dimensions

    Returns:
    ----------
    list of outputs, as [loc_preds, cls_preds, anchor_boxes]
    loc_preds : localization regression prediction
    cls_preds : classification prediction
    anchor_boxes : generated anchor boxes
    """
    sizes=config['anchor_sizes']
    ratios=config['anchor_ratios']
    num_channels=config['arm_channels']
    steps=config['arm_anchor_steps']
    assert len(layers) > 0, "from_layers must not be empty list"
    assert num_classes > 0, \
        "num_classes {} must be larger than 0".format(num_classes)

    assert len(ratios) > 0, "aspect ratios must not be empty list"
    if not isinstance(ratios[0], list):
        # provided only one ratio list, broadcast to all from_layers
        ratios = [ratios] * len(layers)
    assert len(ratios) == len(layers), \
        "ratios and from_layers must have same length"
    assert len(sizes) == len(layers), \
        "sizes and from_layers must have same length but got %d and %d" % (len(sizes),len(layers))

    normalization = config['normalizations']
    if not isinstance(normalization, list):
        normalization = [normalization] * len(layers)
    assert len(normalization) == len(layers)

    assert sum(x > 0 for x in normalization) <= len(num_channels), \
        "must provide number of channels for each normalized layer"

    for k, from_layer in enumerate(layers):
        from_name = from_layer.name
        # normalize
        if normalization[k] > 0:
            from_layer = tf.nn.l2_normalize(from_layer, axis=0,
                                            name="{}_norm".format(from_name).replace(':','_'))
            scale = tf.constant(normalization[k], 
                            name="{}_scale".format(from_name).replace(':','_'))
            scale = tf.cast(scale, from_layer.dtype)
            from_layer = tf.multiply(scale, from_layer)
            layers[k] = from_layer

    interm_layer_channel = config['interm_layer_channel']
    odm_layers = construct_refinedet(layers)
    arm_loc_layers, arm_cls_layers = getpred(config, layers, 1, sizes, ratios, mode='arm', clip=False, 
                                interm_layer_channel=interm_layer_channel, steps=steps)
    odm_loc_layers, odm_cls_layers = getpred(config, odm_layers, num_classes, sizes, ratios, mode='odm', 
                                clip=False, interm_layer_channel=interm_layer_channel, steps=steps)
    return [arm_loc_layers, arm_cls_layers, odm_loc_layers, odm_cls_layers]

def concat_preds(loc_preds_layers, cls_preds_layers, mode):
    loc_preds_layers_m = []
    cls_preds_layers_m = []
    for ii, (loc_pred,cls_pred) in enumerate(zip(loc_preds_layers, cls_preds_layers)):
        lshape = loc_pred.get_shape().as_list()
        cshape = cls_pred.get_shape().as_list()
        num_anchors = lshape[-1]//4
        num_classes = cshape[-1]//num_anchors
        loc_pred = tf.reshape(loc_pred,[-1,lshape[1]*lshape[2]*num_anchors,4])
        cls_pred = tf.reshape(cls_pred,[-1,cshape[1]*cshape[2]*num_anchors,num_classes])
        loc_preds_layers_m.append(loc_pred)
        cls_preds_layers_m.append(cls_pred)
    loc_preds = tf.concat(loc_preds_layers_m, axis=1, name="{}_multibox_loc".format(mode))
    cls_preds = tf.concat(cls_preds_layers_m, axis=1, name="{}_multibox_cls".format(mode))
    # cls_preds = tf.transpose(cls_preds, perm=(0, 2, 1),)
    return loc_preds, cls_preds
# ================================================================================
#对label和bbox进行编码
def anchor_match(labels, bboxes, anchors, config, anchor_for, threshold=0.5, scope=None):
        """Encode labels and bounding boxes.
        """
        num_classes = config['num_classes']
        no_annotation_label = config['no_annotation_label']  #没有注释的标签
        anchor_scaling = config['anchor_scaling']
        return ssd_anchor_match(
            labels, bboxes, anchors,
            num_classes,
            no_annotation_label,
            anchor_for=anchor_for,
            anchor_scaling=anchor_scaling,
            ignore_threshold=0.5,
            scope=scope)
#从网络的一层中用ssd ancchors对gtlabel个bbox编码
def arm_anchor_match_layer(gtlabels, gtboxes,
                               anchors_layer,
                               num_classes,
                               no_annotation_label,
                               ignore_threshold=0.5,
                               prior_scaling=[0.1, 0.1, 0.2, 0.2],
                               dtype=tf.float32):
    """Encode groundtruth labels and bounding boxes using SSD anchors from
    one layer.

    Arguments:
      labels: 1D Tensor(int64) containing groundtruth labels;
      bboxes: Nx4 Tensor(float) with bboxes relative coordinates;
      anchors_layer: Numpy array with layer anchors;
      matching_threshold: Threshold for positive match with groundtruth bboxes;
      prior_scaling: Scaling of encoded coordinates.

    Return:
      (target_labels, target_localizations, target_scores): Target Tensors.
      target_labels: [feat_w, feat_h, num_anchors] Tensor
      target_localizations: [feat_w, feat_h, num_anchors, 4 coords]
      target_scores: same as labels
    """
    gtboxes = tf.cast(gtboxes, dtype)
    gtlabels = tf.cast(gtlabels, dtype)
    # Anchors coordinates and volume.
    yref, xref, href, wref = anchors_layer # yref/xref:[feat_w,feat_h,1],href/wref:[num_anchors,]
    ymin = yref - href / 2. # [feat_w,feat_h,num_anchors]
    xmin = xref - wref / 2.
    ymax = yref + href / 2.
    xmax = xref + wref / 2.
    vol_anchors = (xmax - xmin) * (ymax - ymin)   #计算anchor面积

    # Initialize tensors...
    shape = (yref.shape[0], yref.shape[1], href.size)
    feat_labels = tf.zeros(shape, dtype=dtype)
    feat_scores = tf.zeros(shape, dtype=dtype)

    feat_ymin = tf.zeros(shape, dtype=dtype)
    feat_xmin = tf.zeros(shape, dtype=dtype)
    feat_ymax = tf.ones(shape, dtype=dtype)
    feat_xmax = tf.ones(shape, dtype=dtype)
#计算一个gtbox和anchor的iou交并比
    def jaccard_with_anchors(bbox): # IOU
        """Compute jaccard score between a box and the anchors.
        """
        int_ymin = tf.maximum(ymin, bbox[0])
        int_xmin = tf.maximum(xmin, bbox[1])
        int_ymax = tf.minimum(ymax, bbox[2])
        int_xmax = tf.minimum(xmax, bbox[3])
        h = tf.maximum(int_ymax - int_ymin, 0.)
        w = tf.maximum(int_xmax - int_xmin, 0.)
        # Volumes.
        inter_vol = h * w  #交
        union_vol = vol_anchors - inter_vol \
            + (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])  #并
        jaccard = tf.div(inter_vol, union_vol) #iou
        return jaccard
    #计算一个gtbox和anchor 的面积交/anchor面积
    def intersection_with_anchors(bbox):
        """Compute intersection between score a box and the anchors.
        """
        int_ymin = tf.maximum(ymin, bbox[0])
        int_xmin = tf.maximum(xmin, bbox[1])
        int_ymax = tf.minimum(ymax, bbox[2])
        int_xmax = tf.minimum(xmax, bbox[3])
        h = tf.maximum(int_ymax - int_ymin, 0.)
        w = tf.maximum(int_xmax - int_xmin, 0.)
        inter_vol = h * w
        scores = tf.div(inter_vol, vol_anchors)
        return scores

    def condition(i, feat_labels, feat_scores,
                  feat_ymin, feat_xmin, feat_ymax, feat_xmax):
        """Condition: check label index.
        """
        r = tf.less(i, tf.shape(gtlabels))
        return r[0]

    def body(i, feat_labels, feat_scores,
             feat_ymin, feat_xmin, feat_ymax, feat_xmax):
        """
        loop body, iterate each ground truth box and label, 
                    and match anchors to it
                    each GT box has at least one anchors,一个btbox至少有一个anxhor，一个anchor'只能有一个gtbox
                    and each anchor matches only one GT box
        Body: update feature labels, scores and bboxes.
        Follow the original SSD paper for that purpose:
          - assign values when jaccard > 0.5;
          - only update if beat the score of other bboxes.
        """
        label = gtlabels[i]
        bbox = gtboxes[i]
        jaccard = jaccard_with_anchors(bbox) # IOU
        ### Mask: check threshold + scores + no annotations + num_classes.
        ## check if the IOU is larger than former IOU of another GT box
        mask = tf.greater(jaccard, feat_scores)  #featurescore 初始化为0，一个gtbox何以又多个anxhor对应，
        # 所以要选iou大的
        # mask = tf.logical_and(mask, tf.greater(jaccard, matching_threshold))
        mask = tf.logical_and(mask, feat_scores > -0.5) # ???
        mask = tf.logical_and(mask, label < num_classes) # ???
        fmask = tf.cast(mask, dtype)
        # Update values using mask.
        feat_labels = fmask * label + (1 - fmask) * feat_labels  #根据mask给anchor打标签
        feat_scores = tf.where(mask, jaccard, feat_scores)  #

        feat_ymin = fmask * bbox[0] + (1 - fmask) * feat_ymin
        feat_xmin = fmask * bbox[1] + (1 - fmask) * feat_xmin
        feat_ymax = fmask * bbox[2] + (1 - fmask) * feat_ymax
        feat_xmax = fmask * bbox[3] + (1 - fmask) * feat_xmax

        # Check no annotation label: ignore these anchors...
        # interscts = intersection_with_anchors(bbox)
        # mask = tf.logical_and(interscts > ignore_threshold,
        #                       label == no_annotation_label)
        # # Replace scores by -1.
        # feat_scores = tf.where(mask, -tf.cast(mask, dtype), feat_scores)

        return [i+1, feat_labels, feat_scores,
                feat_ymin, feat_xmin, feat_ymax, feat_xmax]
    # Main loop definition.
    
    i = 0
    [i, feat_labels, feat_scores,
     feat_ymin, feat_xmin,
     feat_ymax, feat_xmax] = tf.while_loop(condition, body,
                                           [i, feat_labels, feat_scores,
                                            feat_ymin, feat_xmin,
                                            feat_ymax, feat_xmax])
    # Transform to center / size.
    feat_cy = (feat_ymax + feat_ymin) / 2.
    feat_cx = (feat_xmax + feat_xmin) / 2.
    feat_h = feat_ymax - feat_ymin
    feat_w = feat_xmax - feat_xmin
    # Encode features.
    feat_cy = (feat_cy - yref) / href / prior_scaling[0]
    feat_cx = (feat_cx - xref) / wref / prior_scaling[1]
    feat_h = tf.log(feat_h / href) / prior_scaling[2]
    feat_w = tf.log(feat_w / wref) / prior_scaling[3]
    # Use SSD ordering: x / y / w / h instead of ours.
    feat_localizations = tf.stack([feat_cx, feat_cy, feat_w, feat_h], axis=-1) # [feat_w, feat_h, num_anchors, num_coords(4)]
    return feat_labels, feat_localizations, feat_scores
#定义目标检测模块匹配
def odm_anchor_match_layer(gtlabels, gtboxes,
                            anchors_layer,
                            num_classes,
                            no_annotation_label,
                            ignore_threshold=0.5,
                            anchor_scaling=[0.1, 0.1, 0.2, 0.2],
                            dtype=tf.float32):
    """Encode groundtruth labels and bounding boxes using SSD anchors from
    one layer.

    Arguments:
      labels: (batch, N_gt) Tensor(int64) containing groundtruth labels;
      bboxes: (batch, N_gt, 4) Tensor(float) with bboxes relative coordinates;
      anchors_layer: [4,cell_width,cell_height,num_anchors]/[batch,cell_width,cell_height,num_anchors,4] 
                            for arm/odm
                            Numpy array with layer anchors;
      matching_threshold: Threshold for positive match with groundtruth bboxes;
      prior_scaling: Scaling of encoded coordinates.

    Return:
      (target_labels, target_locations, target_scores): Target Tensors.
            target_labels: [w,h,num_anchors], the target class number of each anchor
                            (0 indicate the background and 1-num_class indicate the specific class)
            target_locations: [w,h,]
    """
    # Anchors coordinates and volume.
    gtboxes = tf.cast(gtboxes, dtype)
    gtlabels = tf.cast(gtlabels, dtype)
    xref, yref, wref, href = tf.split(anchors_layer, axis=-1, num_or_size_splits=4)
    # xref = xref * anchor_scaling[0] * anchors_layer[2] + anchors_layer[0]
    # yref = yref * anchor_scaling[1] * anchors_layer[3] + anchors_layer[1] 
    # wref = tf.exp(wref * anchor_scaling[2]) * anchors_layer[2]
    # href = tf.exp(href * anchor_scaling[3]) * anchors_layer[3]
    feat_labels = tf.zeros_like(xref)
    coord_shape = xref.get_shape().as_list() #(batch,w,h,anchor_number)
    ymin = yref - href / 2.
    xmin = xref - wref / 2.
    ymax = yref + href / 2.
    xmax = xref + wref / 2.
    vol_anchors = (xmax - xmin) * (ymax - ymin)
    # Initialize tensors...
    feat_scores = tf.zeros_like(feat_labels)

    feat_ymin = tf.zeros_like(feat_labels)
    feat_xmin = tf.zeros_like(feat_labels)
    feat_ymax = tf.ones_like(feat_labels)
    feat_xmax = tf.ones_like(feat_labels)

    def jaccard_with_anchors(bbox): # IOU
        """Compute jaccard score between a box and the anchors.
        """
        int_ymin = tf.maximum(ymin, bbox[:,:,:,0]) # ((1 or batch,feat_w,feat_h,anchor_num), (batch,1,1,1))
        int_xmin = tf.maximum(xmin, bbox[:,:,:,1])
        int_ymax = tf.minimum(ymax, bbox[:,:,:,2])
        int_xmax = tf.minimum(xmax, bbox[:,:,:,3])
        h = tf.maximum(int_ymax - int_ymin, 0.)
        w = tf.maximum(int_xmax - int_xmin, 0.)
        # Volumes.
        inter_vol = h * w
        union_vol = vol_anchors - inter_vol \
            + (bbox[:,:,:,2] - bbox[:,:,:,0]) * (bbox[:,:,:,3] - bbox[:,:,:,1])
        jaccard = tf.div(inter_vol, union_vol)
        return jaccard # ((batch), feat_w, feat_h, anchor_num)
    # 定义一个gtbox和所有anchor的交
    def intersection_with_anchors(bbox):
        """Compute intersection score between a gbox and the anchors.
        """
        int_ymin = tf.maximum(ymin, bbox[0])
        int_xmin = tf.maximum(xmin, bbox[1])
        int_ymax = tf.minimum(ymax, bbox[2])
        int_xmax = tf.minimum(xmax, bbox[3])
        h = tf.maximum(int_ymax - int_ymin, 0.)
        w = tf.maximum(int_xmax - int_xmin, 0.)
        inter_vol = h * w
        scores = tf.div(inter_vol, vol_anchors)
        return scores

    def condition(i, feat_labels, feat_scores,
                  feat_ymin, feat_xmin, feat_ymax, feat_xmax):
        """Condition: check label index. i indicate the i_th class
        """
        r = tf.less(i, tf.shape(gtlabels))
        return r[0]

    def body(i, feat_labels, feat_scores,
             feat_ymin, feat_xmin, feat_ymax, feat_xmax):
        """
        loop body, iterate each ground truth box and label, 
                    and match anchors to it
                    each GT box has at least one anchors, 
                    and each anchor matches only one GT box
        Body: update feature labels, scores and bboxes.
        Follow the original SSD paper for that purpose:
          - assign values when jaccard > 0.5;
          - only update if beat the score of other bboxes.
        """
        # Jaccard score.
        label = tf.expand_dims(tf.expand_dims(gtlabels[:,i],-1),-1) #(batch,)
        bbox = tf.expand_dims(tf.expand_dims(gtboxes[:,i,:],axis=1),axis=1)# (batch,1,1,4)
        jaccard = jaccard_with_anchors(bbox) # IOU ((batch), feat_w, feat_h, anchor_num)
        # Mask: check threshold + scores + no annotations + num_classes.
        mask = tf.greater(jaccard, feat_scores)
        # mask = tf.logical_and(mask, tf.greater(jaccard, matching_threshold))
        mask = tf.logical_and(mask, feat_scores > -0.5)
        mask = tf.logical_and(mask, label < num_classes) #
            
        # imask = tf.cast(mask, tf.int64)
        fmask = tf.cast(mask, dtype)
        # Update values using mask.
        feat_labels = fmask * label + (1 - fmask) * feat_labels
        # replace values in feat_scores with jaccard according to mask
        feat_scores = tf.where(mask, jaccard, feat_scores) 

        feat_ymin = fmask * bbox[:,:,:,0] + (1 - fmask) * feat_ymin
        feat_xmin = fmask * bbox[:,:,:,1] + (1 - fmask) * feat_xmin
        feat_ymax = fmask * bbox[:,:,:,2] + (1 - fmask) * feat_ymax
        feat_xmax = fmask * bbox[:,:,:,3] + (1 - fmask) * feat_xmax

        # Check no annotation label: ignore these anchors...
        # interscts = intersection_with_anchors(bbox)
        # mask = tf.logical_and(interscts > ignore_threshold,
        #                       label == no_annotation_label)
        # # Replace scores by -1.
        # feat_scores = tf.where(mask, -tf.cast(mask, dtype), feat_scores)

        return [i+1, feat_labels, feat_scores,
                feat_ymin, feat_xmin, feat_ymax, feat_xmax]
    # Main loop definition.
    i = 0
    [i, feat_labels, feat_scores,
     feat_ymin, feat_xmin,
     feat_ymax, feat_xmax] = tf.while_loop(condition, body,
                                           [i, feat_labels, feat_scores,
                                            feat_ymin, feat_xmin,
                                            feat_ymax, feat_xmax])
    # Transform to center / size.
    feat_cy = (feat_ymax + feat_ymin) / 2.
    feat_cx = (feat_xmax + feat_xmin) / 2.
    feat_h = feat_ymax - feat_ymin
    feat_w = feat_xmax - feat_xmin
    # Encode features.
    feat_cy = (feat_cy - yref) / href / anchor_scaling[0]
    feat_cx = (feat_cx - xref) / wref / anchor_scaling[1]
    feat_h = tf.log(feat_h / href) / anchor_scaling[2]
    feat_w = tf.log(feat_w / wref) / anchor_scaling[3]
    # Use SSD ordering: x / y / w / h instead of ours.
    feat_localizations = tf.stack([feat_cx, feat_cy, feat_w, feat_h], axis=-1)
    return feat_labels, feat_localizations, feat_scores
#定义ssd网络anchor match函数，用ssdnetanchor对gtlablel和bbox进行编码
def ssd_anchor_match(labels,
                         bboxes,
                         anchors,
                         num_classes,
                         no_annotation_label,
                         ignore_threshold=0.5,
                         anchor_for='arm',
                         anchor_scaling=[0.1, 0.1, 0.2, 0.2],
                         dtype=tf.float32,
                         scope='ssd_anchor_match'):
    """Encode groundtruth labels and bounding boxes using SSD net anchors.
    Encoding boxes for all feature layers.

    Arguments:
      labels: 1D (N,) Tensor(int64) containing groundtruth labels;
      bboxes: Nx4 Tensor(float) with ground truth bboxes relative coordinates;
      anchors: List of Numpy array with layer anchors;
      matching_threshold: Threshold for positive match with groundtruth bboxes;
      prior_scaling: Scaling of encoded coordinates.

    Return:
      (target_labels, target_localizations, target_scores):
        Each element is a list of target Tensors.
    """
    assert anchor_for in ['arm','odm'], 'anchor_for argument illegal, but got ' + str(anchor_for)
    match_fn = arm_anchor_match_layer if anchor_for == 'arm' else odm_anchor_match_layer
    with tf.name_scope(scope):
        target_labels = []
        target_localizations = []
        target_scores = []
        for i, anchors_layer in enumerate(anchors):
            with tf.name_scope('bboxes_encode_block_%i' % i):
                t_labels, t_loc, t_scores = \
                    match_fn(labels, bboxes, anchors_layer,
                                        num_classes, no_annotation_label,
                                        ignore_threshold,
                                        anchor_scaling, dtype)
                target_labels.append(t_labels)
                target_localizations.append(t_loc)
                target_scores.append(t_scores)
        return target_labels, target_localizations, target_scores

#===================================================================================
#定义修正anchor的函数
def refine_anchor_layer(anchors_layer, arm_loc_preds, anchor_scaling=[0.1,0.1,0.2,0.2]):
    '''
    input:
        anchors_layer: (yref,xref,href,wref)tuple, the original anchor boxes,
        arm_loc_preds: (batch, feat_w, feat_h, num_anchors*4) outputs from arm layers
    return:
        refined anchor: (batch, feat_w, feat_h, num_anchors*4)expand 'batch' dimension in the 0 axis
    '''
    aloc_shape = arm_loc_preds.get_shape().as_list()
    xref, yref, wref, href = anchors_layer
    # batch_size = aloc_shape[0]
    # arm_anchor_boxes = tf.concat([arm_anchor_boxes_loc] * batch_size, axis=0)

    # arm_anchor_boxes_bs = tf.split(
    #     arm_anchor_boxes_loc, num_or_size_splits=4, axis=-1)
    # decode matched arm_anchor locations
    # center_x_a = arm_anchor_boxes_bs[0] * height_a * anchor_scaling[0]
    # center_y_a = arm_anchor_boxes_bs[1] * width_a * anchor_scaling[1]
    # width_a = tf.exp(arm_anchor_boxes_bs[2] * anchor_scaling[2]) * arm_anchor_boxes_bs[2] #/ 2.0
    # height_a = tf.exp(arm_anchor_boxes_bs[3] * anchor_scaling[3]) * arm_anchor_boxes_bs[3]

    arm_loc_preds = tf.reshape(arm_loc_preds,
                shape=[-1, aloc_shape[1],aloc_shape[2], 4, aloc_shape[3]//4])
    arm_loc_preds_bs = tf.unstack(arm_loc_preds, axis=-2)

    center_x_preds = arm_loc_preds_bs[0]
    center_y_preds = arm_loc_preds_bs[1]
    width_preds = arm_loc_preds_bs[2]
    height_preds = arm_loc_preds_bs[3]

    # decode and refine anchors 解码，decode
    coord_x = center_x_preds * href * anchor_scaling[0] + xref 
    coord_y = center_y_preds * wref * anchor_scaling[1] + yref
    coord_width = tf.exp(width_preds * anchor_scaling[2]) * wref# / 2.0
    coord_heigt = tf.exp(height_preds * anchor_scaling[3]) * href #/ 2.0
    # print('1.',coord_x.get_shape().as_list(),
    #         coord_y.get_shape().as_list(),
    #         coord_width.get_shape().as_list(),
    #         coord_heigt.get_shape().as_list())

    refined_anchor = tf.concat([coord_x, coord_y, coord_width, coord_heigt], axis=-1)
    # print('2.',refined_anchor.get_shape().as_list())
    return refined_anchor
#arm网络对anchornbox进行修正，
def refine_anchor(anchor_location_all_layers, loc_pred_all_layers):
    refined_anchors = []
    for ii, anchor_location in enumerate(anchor_location_all_layers):
        loc_pred = loc_pred_all_layers[ii]
        refined_anchors.append(refine_anchor_layer(anchor_location,loc_pred))
    return refined_anchors

# ==================================================================================
def ssd_bboxes_select_layer(predictions_layer, localizations_layer,
                               select_threshold=None,
                               num_classes=21,
                               ignore_class=0,
                               scope=None):
    """Extract classes, scores and bounding boxes from features in one layer.
    Batch-compatible: inputs are supposed to have batch-type shapes.

    Args:
      predictions_layer: A SSD prediction layer;
      localizations_layer: A SSD localization layer;
      select_threshold: Classification threshold for selecting a box. All boxes
        under the threshold are set to 'zero'. If None, no threshold applied.
    Return:
      d_scores, d_bboxes: Dictionary of scores and bboxes Tensors of
        size Batches X N x 1 | 4. Each key corresponding to a class.
    """
    select_threshold = 0.0 if select_threshold is None else select_threshold
    with tf.name_scope(scope, 'ssd_bboxes_select_layer',
                       [predictions_layer, localizations_layer]):
        # Reshape features: Batches x N x N_labels | 4
        p_shape = tensors.get_shape(predictions_layer)
        predictions_layer = tf.reshape(predictions_layer,
                                       tf.stack([p_shape[0], -1, p_shape[-1]]))
        l_shape = tensors.get_shape(localizations_layer)
        localizations_layer = tf.reshape(localizations_layer,
                                         tf.stack([l_shape[0], -1, l_shape[-1]]))

        d_scores = {}
        d_bboxes = {}
        for c in range(0, num_classes):
            if c != ignore_class:
                # Remove boxes under the threshold.
                scores = predictions_layer[:, :, c]
                fmask = tf.cast(tf.greater_equal(scores, select_threshold), scores.dtype)
                scores = scores * fmask
                bboxes = localizations_layer * tf.expand_dims(fmask, axis=-1)
                # Append to dictionary.
                d_scores[c] = scores
                d_bboxes[c] = bboxes

        return d_scores, d_bboxes

def ssd_bboxes_select(predictions_net, localizations_net,
                         select_threshold=None,
                         num_classes=21,
                         ignore_class=0,
                         scope=None):
    """Extract classes, scores and bounding boxes from network output layers.
    Batch-compatible: inputs are supposed to have batch-type shapes.

    Args:
      predictions_net: List of SSD prediction layers;
      localizations_net: List of localization layers;
      select_threshold: Classification threshold for selecting a box. All boxes
        under the threshold are set to 'zero'. If None, no threshold applied.
    Return:
      d_scores, d_bboxes: Dictionary of scores and bboxes Tensors of
        size Batches X N x 1 | 4. Each key corresponding to a class.
    """
    with tf.name_scope(scope, 'ssd_bboxes_select',
                       [predictions_net, localizations_net]):
        l_scores = []
        l_bboxes = []
        for i in range(len(predictions_net)):
            scores, bboxes = ssd_bboxes_select_layer(predictions_net[i],
                                                        localizations_net[i],
                                                        select_threshold,
                                                        num_classes,
                                                        ignore_class)
            l_scores.append(scores)
            l_bboxes.append(bboxes)
        # Concat results.
        d_scores = {}
        d_bboxes = {}
        for c in l_scores[0].keys():
            ls = [s[c] for s in l_scores]
            lb = [b[c] for b in l_bboxes]
            d_scores[c] = tf.concat(ls, axis=1)
            d_bboxes[c] = tf.concat(lb, axis=1)
        return d_scores, d_bboxes

