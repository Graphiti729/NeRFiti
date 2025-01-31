import torch
import torch.nn as nn
from model_nerfies._utils import init_activation


class MLP(nn.Module):
    def __init__(self,  depth, in_feature, hidden_dim, hidden_activation, output_feature, output_activation, skips):
        super(MLP, self).__init__()
        self.depth = depth

        self.in_feature = in_feature

        self.hidden_dim = hidden_dim
        # self.hidden_init = hidden_init
        self.hidden_activtation = init_activation(hidden_activation)

        # self.output_init = output_init
        self.output_feature = output_feature
        self.output_activation = init_activation(output_activation)

        # self.use_bias = use_bias
        self.skips = skips

        self.layers = nn.ModuleList(
            [nn.Linear(self.in_feature, self.hidden_dim)] +[
                nn.Linear(self.hidden_dim, self.hidden_dim)
                if i+1 not in self.skips else # i+1로 해야 index가 맞는듯
                nn.Linear(self.hidden_dim + self.in_feature, self.hidden_dim) 
                for i in range(self.depth - 1)])
        for layer in self.layers:
            nn.init.xavier_uniform(layer.weight)

        self.output_layer = nn.Linear(self.hidden_dim, self.output_feature)
        nn.init.xavier_uniform(self.output_layer.weight)

    def forward(self, x):
        inputs = x
        for i, layer in enumerate(self.layers):
            if i in self.skips:
                x = torch.concat((x, inputs), dim=-1)
            x = layer(x)
            x = self.hidden_activtation(x)

        if self.output_feature > 0:
            x = self.output_layer(x)
        
        if self.output_activation is not None:
            x = self.output_activation(x)
        return x
    

class NeRFMLP(nn.Module):
    """
        Attributes:
            nerf_trunk_depth: int, the depth of the first part of MLP.
            nerf_trunk_width: int, the width of the first part of MLP.
            nerf_rgb_branch_depth: int, the depth of the second part of MLP.
            nerf_rgb_branch_width: int, the width of the second part of MLP.
            activation: function, the activation function used in the MLP.
            skips: which layers to add skip layers to.
            alpha_channels: int, the number of alpha_channelss.
            rgb_channels: int, the number of rgb_channelss.
            condition_density: if True put the condition at the begining which
            conditions the density of the field.

            alpha_condition: a condition array provided to the alpha branch.
            rgb_condition: a condition array provided in the RGB branch.
    """
    def __init__(self, mlp_trunk_args,  mlp_rgb_args, mlp_alpha_args, use_alpha_condition, use_rgb_condition,
                 rgb_channels, alpha_channels,
                 **kwargs):
        super(NeRFMLP, self).__init__()

        self.trunk_mlp = MLP(**mlp_trunk_args)

        self.rgb_mlp = MLP(**mlp_rgb_args)
        
        self.alpha_mlp = MLP(**mlp_alpha_args)

        self.rgb_channels = rgb_channels
        self.alpha_channels = alpha_channels

        if use_alpha_condition or use_rgb_condition:
            self.bottleneck = nn.Linear(mlp_trunk_args['hidden_dim'], mlp_trunk_args['hidden_dim'])

    def forward(self, x, trunk_condition, alpha_condition, rgb_condition):
        """
            Multi-layer perception for nerf.

            Args:
            x: sample points with shape [batch, num_coarse_samples, feature].
            trunk_condition: a condition array provided to the trunk.
            alpha_condition: a condition array provided to the alpha branch.
            rgb_condition: a condition array provided in the RGB branch.

            Returns:
            raw: [batch, num_coarse_samples, rgb_channels+alpha_channels].
        """
        feature_dim = x.shape[-1]
        num_samples = x.shape[1]
        x = x.reshape([-1, feature_dim])
        if trunk_condition is not None:
            trunk_condition = broadcast_condition(trunk_condition, num_samples)
            trunk_input = torch.concat((x, trunk_condition), dim=-1)
        else:
            trunk_input = x
        x = self.trunk_mlp(trunk_input)

        if (alpha_condition is not None) or (rgb_condition is not None):
            bottleneck = self.bottleneck(x)

            if alpha_condition is not None:
                alpha_condition = broadcast_condition(alpha_condition, num_samples)
                alpha_input = torch.cat([bottleneck, alpha_condition], dim=-1)
            else:
                alpha_input = x
            alpha = self.alpha_mlp(alpha_input)

            if rgb_condition is not None:
                rgb_condition = broadcast_condition(rgb_condition, num_samples)
                rgb_input = torch.cat([bottleneck, rgb_condition], dim=-1)
            else:
                rgb_input = x
            rgb = self.rgb_mlp(rgb_input)

        return {
            'rgb': rgb.reshape((-1, num_samples, self.rgb_channels)),
            'alpha': alpha.reshape((-1, num_samples, self.alpha_channels)),
        }


def broadcast_condition(c, num_samples):
    # Broadcast condition from [batch, feature] to
    # [batch, num_coarse_samples, feature] since all the samples along the
    # same ray has the same viewdir.
    c = torch.tile(c[:, None, :], (1, num_samples, 1))
    # Collapse the [batch, num_coarse_samples, feature] tensor to
    # [batch * num_coarse_samples, feature] to be fed into nn.Dense.
    c = c.reshape([-1, c.shape[-1]])
    return c
