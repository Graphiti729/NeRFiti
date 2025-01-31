import torch
import torch.nn as nn
from model_nerfies import warping
from model_nerfies.modules import NeRFMLP
from model_nerfies.sampler import sample_along_rays, sample_pdf
from model_nerfies.rendering import render_samples
from model_nerfies.embed import SinusoidalEncoder, GloEncoder
from dataset import NerfiesDataSet
from functionals import log_internal


def get_model(model_cfg, render_cfg, run_cfg, dataset: NerfiesDataSet):
    backbone_cfg = model_cfg['backbone']
    warp_cfg = model_cfg['warp']

    time_encoder_args = dict(warp_cfg['encoder_args'], ** {
        'scale': warp_cfg['time_encoder_args']['scale'],
        'mlp_args': dict(warp_cfg['mlp_args'], **warp_cfg['time_encoder_args']['time_mlp_args'])
    })
    field_args = {
        'points_encoder_args': dict(warp_cfg['points_encoder_args'], **warp_cfg['encoder_args']),
        'metadata_encoder_type': model_cfg['warp_metadata_encoder_type'],
        # NOTE : appearance인지 camera인지 구분해야 한다.
        'glo_encoder_args': {'num_embeddings': dataset.num_warp_embeddings,
                             'embedding_dim': warp_cfg['num_warp_features']},
        'time_encoder_args': time_encoder_args,
        'mlp_trunk_args': dict(warp_cfg['mlp_args'], **warp_cfg['mlp_trunk_args']),
        'mlp_branch_w_args': dict(warp_cfg['mlp_args'], **warp_cfg['mlp_branch_w_args']),
        'mlp_branch_v_args': dict(warp_cfg['mlp_args'], **warp_cfg['mlp_branch_v_args']),
        'use_pivot': False,
        'mlp_branch_p_args': dict(warp_cfg['mlp_args'], **warp_cfg['mlp_branch_p_args']),
        'use_translation': False,
        'mlp_branch_t_args': dict(warp_cfg['mlp_args'], **warp_cfg['mlp_branch_t_args']),

    }
    warp_field_args = {
        "field_type": model_cfg['warp']['warp_field_type'],
        'field_args': field_args,
        'num_batch_dims': 0,
    }

    nerfmlp_args = {
        'mlp_trunk_args': backbone_cfg['trunk'],
        'mlp_rgb_args': backbone_cfg['rgb'],
        'mlp_alpha_args': backbone_cfg['alpha'],
        'use_rgb_condition': model_cfg['use_rgb_condition'],
        'use_alpha_condition': model_cfg['use_alpha_condition'],
        'rgb_channels': backbone_cfg['rgb_channels'],
        'alpha_channels': backbone_cfg['alpha_channels'],
    }

    _cfg = {
        "use_viewdirs": model_cfg['use_viewdirs'],
        "use_fine_samples": render_cfg['use_fine_samples'],
        "coarse_args": nerfmlp_args,
        "fine_args": nerfmlp_args,

        "use_warp": model_cfg['warp']['use_warp'],
        "warp_field_args": warp_field_args,
        "use_warp_jacobian": run_cfg['use_elastic_loss'],
        'warp_metadata_encoder_type': model_cfg['warp_metadata_encoder_type'],

        "use_appearance_metadata": dataset.use_appearance_id,
        "appearance_encoder_args": {'num_embeddings': dataset.num_appearance_embeddings,
                                    'embedding_dim': model_cfg['appearance_metadata_dims']},

        "use_camera_metadata": dataset.use_camera_id,
        "camera_encoder_args": {'num_embeddings': dataset.num_camera_embeddings,
                                'embedding_dim': model_cfg['camera_metadata_dims']},

        "use_trunk_condition": model_cfg['use_trunk_condition'],
        "use_alpha_condition": model_cfg['use_alpha_condition'],

        "point_encoder_args": dict(backbone_cfg['encoder_args'], **{'num_freqs': model_cfg['num_nerf_point_freqs']}),
        "viewdir_encoder_args": dict(backbone_cfg['encoder_args'], **{'num_freqs': model_cfg['num_nerf_viewdir_freqs']}),
    }
    model = Nerfies(**_cfg)
    return model


class Nerfies(nn.Module):
    def __init__(self, use_viewdirs,
                 use_fine_samples, coarse_args, fine_args,
                 use_warp, warp_field_args, use_warp_jacobian, warp_metadata_encoder_type,
                 use_appearance_metadata, appearance_encoder_args,
                 use_camera_metadata, camera_encoder_args,
                 use_trunk_condition, use_alpha_condition,
                 point_encoder_args, viewdir_encoder_args,
                 ):
        super(Nerfies, self).__init__()

        self.use_viewdirs = use_viewdirs

        self.use_warp = use_warp
        self.use_warp_jacobian = use_warp_jacobian
        self.warp_metadata_encoder_type = warp_metadata_encoder_type
        # self.warp_field_coarse = None
        # self.warp_field_fine = None
        self.warp_field = None
        if use_warp:
            # self.warp_field_coarse = create_warp_field(**warp_field_args)
            # self.warp_field_fine = create_warp_field(**warp_field_args)
            self.warp_field = create_warp_field(**warp_field_args)

        # self.point_encoder_coarse = SinusoidalEncoder(**point_encoder_args)
        # self.point_encoder_fine = SinusoidalEncoder(**point_encoder_args)
        self.point_encoder = SinusoidalEncoder(**point_encoder_args)
        self.viewdir_encoder = SinusoidalEncoder(**viewdir_encoder_args)
        self.use_trunk_condition = use_trunk_condition
        self.use_alpha_condition = use_alpha_condition        
        
        self.appearance_encoder = None
        self.use_appearance_metadata = use_appearance_metadata
        if use_appearance_metadata:
            self.appearance_encoder = GloEncoder(**appearance_encoder_args)
        
        self.camera_encoder = None
        self.use_camera_metadata = use_camera_metadata
        if use_camera_metadata:
            self.camera_encoder = GloEncoder(**camera_encoder_args)
        
        self.mlps = {'coarse': NeRFMLP(**coarse_args)}

        self.use_fine_samples = use_fine_samples
        if use_fine_samples:
            self.mlps['fine'] = NeRFMLP(**fine_args)

        log_internal("[Model] Loaded")

    def get_params(self):
        params = list(self.parameters())
        if self.use_warp:
            for warp_mlp in self.warp_field.branches.values():
                params += list(warp_mlp.parameters())
        for nerfies_mlp in self.mlps.values():
            params += list(nerfies_mlp.parameters())
        return params

    def get_condition(self, viewdirs, metadata, is_metadata_encoded):
        """Create the condition inputs for the NeRF template."""
        trunk_conditions = []
        alpha_conditions = []
        rgb_conditions = []            

        # Point attribute predictions
        if self.use_viewdirs:
            viewdirs_embed = self.viewdir_encoder(viewdirs)
            rgb_conditions.append(viewdirs_embed)

        if self.use_appearance_metadata:
            if is_metadata_encoded:
                appearance_code = metadata['appearance']
            else:
                appearance_code = self.appearance_encoder(metadata['appearance'])
            if self.use_trunk_condition:
                trunk_conditions.append(appearance_code)
            if self.use_alpha_condition:
                alpha_conditions.append(appearance_code)
            if self.use_alpha_condition:
                rgb_conditions.append(appearance_code)
        
        if self.use_camera_metadata:
            if is_metadata_encoded:
                camera_code = metadata['camera']
            else:
                # NOTE: torch에서 Embedding은 마지막 layer가 추가가 되는 것이다.
                # camera_code = self.camera_encoder(metadata['camera'])
                camera_code = self.camera_encoder(metadata['camera'].squeeze(-1))
            rgb_conditions.append(camera_code)

        # The condition inputs have a shape of (B, C) now rather than (B, S, C)
        # since we assume all samples have the same condition input. We might want
        # to change this later.
        trunk_conditions = (torch.concat(trunk_conditions, dim=-1) if trunk_conditions else None)
        alpha_conditions = (torch.concat(alpha_conditions, dim=-1) if alpha_conditions else None)
        rgb_conditions = (torch.concat(rgb_conditions, dim=-1) if rgb_conditions else None)
        return trunk_conditions, alpha_conditions, rgb_conditions

    def forward(self, 
                rays_o, rays_d, viewdirs, metadata,
                warp_params, is_metadata_encoded,
                return_points, return_weights,
                return_warp_jacobian,
                ray_sampling_args, rendering_args,
                num_coarse_samples, num_fine_samples,
                **kwargs
                ):
        if viewdirs is None:
            viewdirs = rays_d

        out = {}
        coarse_ret = {}
        fine_ret = {}
        # Ray Sampling : Coarse
        ray_sampling_args = dict(ray_sampling_args, **{'rays_o': rays_o, 'rays_d': rays_d, 'num_coarse_samples': num_coarse_samples})
        z_vals, points = sample_along_rays(**ray_sampling_args)
        if return_points:
            out['points'] = points

        trunk_conditions, alpha_conditions, rgb_conditions = self.get_condition(viewdirs, metadata, is_metadata_encoded)

        # Warp rays
        if self.use_warp:
            # metadata_channels = self.num_warp_features if is_metadata_encoded else None
            # warp_metadata = (metadata['time'] if self.warp_metadata_encoder_type == 'time' else metadata['warp'])
            # warp_metadata = torch.broadcast_to(warp_metadata[:, None, :], (points.size()[0], points.size()[1], metadata_channels))
            if is_metadata_encoded:
                metadata_channels = self.num_warp_features
                warp_metadata = metadata['time'] if self.warp_metadata_encoder_type == 'time' else metadata['warp']
                warp_metadata = torch.broadcast_to(warp_metadata[:, None, :],
                                                   (points.size()[0], points.size()[1], metadata_channels))
            else:
                warp_metadata = (metadata['time'] if self.warp_metadata_encoder_type == 'time' else metadata['warp'])
                warp_metadata = torch.broadcast_to(warp_metadata[:, :], (points.size()[0], points.size()[1]))
            warp_out = self.warp_field(
                points,
                warp_metadata,
                warp_params,
                return_warp_jacobian,
                is_metadata_encoded)
            points = warp_out['warped_points']
            if 'jacobian' in warp_out:
                coarse_ret['warp_jacobian'] = warp_out['jacobian']
            if return_points:
                coarse_ret['warped_points'] = warp_out['warped_points']
        
        points_embed = self.point_encoder(points)

        # Ray Colorization : Coarse
        coarse_ret.update(render_samples(
            self.mlps['coarse'],
            points_embed, trunk_conditions, alpha_conditions, rgb_conditions,
            z_vals, rays_d, return_weights,
            **rendering_args
        ))
        out['coarse'] = coarse_ret

        if self.use_fine_samples:
            z_vals_mid = .5 * (z_vals[..., 1:] + z_vals[..., :-1])
            # Ray Sampling : Fine --> Hierarchical sampling
            # NOTE: 여기에서 clone을 안해서 생겼던 문제였다....
            z_vals, points = sample_pdf(z_vals_mid, torch.tensor(coarse_ret['weights'][..., 1:-1]),
                                        rays_o, rays_d, z_vals, num_fine_samples,
                                        rendering_args['use_stratified_sampling'])

            trunk_conditions, alpha_conditions, rgb_conditions = self.get_condition(viewdirs, metadata,
                                                                                    is_metadata_encoded)

            # Warp rays
            if self.use_warp:
                if is_metadata_encoded:
                    metadata_channels = self.num_warp_features
                    warp_metadata = metadata['time'] if self.warp_metadata_encoder_type == 'time' else metadata['warp']
                    warp_metadata = torch.broadcast_to(warp_metadata[:, None, :],
                                                       (points.size()[0], points.size()[1], metadata_channels))
                else:
                    warp_metadata = (metadata['time'] if self.warp_metadata_encoder_type == 'time' else metadata['warp'])
                    warp_metadata = torch.broadcast_to(warp_metadata[:, :], (points.size()[0], points.size()[1]))
                warp_out = self.warp_field(
                    points,
                    warp_metadata,
                    warp_params,
                    return_warp_jacobian,
                    is_metadata_encoded)
                points = warp_out['warped_points']
                if 'jacobian' in warp_out:
                    fine_ret['warp_jacobian'] = warp_out['jacobian']
                if return_points:
                    fine_ret['warped_points'] = warp_out['warped_points']

            points_embed = self.point_encoder(points)

            # Ray Colorization : Fine
            fine_ret.update(render_samples(
                self.mlps['fine'],
                points_embed, trunk_conditions, alpha_conditions, rgb_conditions,
                z_vals, rays_d, return_weights,
                **rendering_args
            ))
        return coarse_ret, fine_ret


def create_warp_field(field_type, field_args, num_batch_dims, **kwargs):
    return warping.create_warp_field(
        field_type=field_type,
        field_args=field_args,
        num_batch_dims=num_batch_dims,
        )




