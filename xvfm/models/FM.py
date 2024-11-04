import torch
from abc import ABC, abstractmethod
from models.OT import OTSampler
from typing import Union


class FlowModel(ABC):
    def __init__(self, nn: torch.nn.Module):
        self.model = nn

    @abstractmethod
    def interpolation(self, x0, x1, t):
        pass

    @abstractmethod
    def integration(self, model, x_0, steps, device=None):
        xt = x_0.to(device) if device else x_0
        delta_t = 1.0 / steps

        trajectory = torch.zeros((steps + 1, *xt.shape), device=xt.device)
        trajectory[0] = xt
        
        time_steps = torch.linspace(0, 1, steps, device=xt.device).unsqueeze(1)

        for k in range(steps):
            t = time_steps[k].expand(xt.shape[0], 1)
            v_t = self.interpolation(xt, t)
            xt = xt + v_t * delta_t
            trajectory[k + 1] = xt

        return trajectory

    @abstractmethod
    def sample_location_and_conditional_flow(self, x0, x1, t=None, return_noise=False):
        pass

    def sample_noise_like(self, x):
        return torch.randn_like(x)
    
    def pad_t_like_x(t, x):
        if isinstance(t, (float, int)):
            return t
        return t.reshape(-1, *([1] * (x.dim() - 1)))



class CFM(FlowModel):
    def __init__(self, model: torch.nn.Module, sigma: Union[float, int] = 0.0):
        super().__init__(model)
        self.sigma = sigma

    def interpolation(self, x_t, t):
        with torch.no_grad():
            return self.model(torch.cat([x_t, t], dim=-1))

    def compute_mu_t(self, x0, x1, t):
        t = self.pad_t_like_x(t, x0)
        return t * x1 + (1 - t) * x0

    def compute_sigma_t(self, t):
        del t
        return self.sigma

    def sample_xt(self, x0, x1, t, epsilon):
        mu_t = self.compute_mu_t(x0, x1, t)
        sigma_t = self.compute_sigma_t(t)
        sigma_t = self.pad_t_like_x(sigma_t, x0)
        return mu_t + sigma_t * epsilon

    def sample_location_and_conditional_flow(self, x0, x1, t=None, return_noise=False):
        if t is None:
            t = torch.rand(x0.shape[0]).type_as(x0)
        assert len(t) == x0.shape[0], "t has to have batch size dimension"

        eps = self.sample_noise_like(x0)
        xt = self.sample_xt(x0, x1, t, eps)

        if return_noise:
            return t, xt, eps
        else:
            return t, xt


class OT_CFM(CFM):
    def __init__(self, model: torch.nn.Module, sigma: Union[float, int] = 0.0):
        super().__init__(model, sigma)
        self.ot_sampler = OTSampler()

    def sample_location_and_conditional_flow(self, x0, x1, t=None, return_noise=False):
        x0, x1 = self.ot_sampler.sample_plan(x0, x1)
        return super().sample_location_and_conditional_flow(x0, x1, t, return_noise)


class VFM(FlowModel):
    def __init__(self, model: torch.nn.Module, sigma: Union[float, int] = 0.0):
        super().__init__(model)
        self.sigma = sigma

    def interpolation(self, x_t, t):
        with torch.no_grad():
            x1 = self.model(torch.cat([x_t, t], dim=-1))
            v_t = (x1 - x_t) / (1 - t)
            return v_t

    def compute_mu_t(self, x0, x1, t):
        t = self.pad_t_like_x(t, x0)
        return t * x1 + (1 - t) * x0

    def compute_sigma_t(self, t):
        del t
        return self.sigma

    def sample_xt(self, x0, x1, t, epsilon):
        mu_t = self.compute_mu_t(x0, x1, t)
        sigma_t = self.compute_sigma_t(t)
        sigma_t = self.pad_t_like_x(sigma_t, x0)
        return mu_t + sigma_t * epsilon

    def sample_location_and_conditional_flow(self, x0, x1, t=None, return_noise=False):
        if t is None:
            t = torch.rand(x0.shape[0]).type_as(x0)
        assert len(t) == x0.shape[0], "t has to have batch size dimension"

        eps = self.sample_noise_like(x0)
        xt = self.sample_xt(x0, x1, t, eps)

        if return_noise:
            return t, xt, eps
        else:
            return t, xt