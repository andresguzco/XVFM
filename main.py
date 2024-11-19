import os
import time
import torch
import argparse
import matplotlib.pyplot as plt

from torchvision import datasets, transforms
from torchvision.transforms import ToPILImage
from torchvision.utils import make_grid

from tqdm import tqdm
from pathlib import Path
from xvfm.models.models import MLP, MLPS
from xvfm.models.unet import UNetModel
from xvfm.models.fm import CFM, OT_CFM, VFM
from xvfm.utils import sample_8gaussians, sample_moons, plot_trajectories


MODEL_MAP = {
    'two_moons': {
        'cfm': {'flow': CFM, 'model': MLP},
        'ot': {'flow': OT_CFM, 'model': MLP},
        'vfm': {
            True: {'flow': VFM, 'model': MLPS},
            False: {'flow': VFM, 'model': MLP}
        }
    },
    'MNIST': {
        'cfm': {'flow': CFM, 'model': UNetModel},
        'ot': {'flow': OT_CFM, 'model': UNetModel},
        'vfm': {
            True: {'flow': VFM, 'model': UNetModel},
            False: {'flow': VFM, 'model': UNetModel}
        }
    }
}

CRITERION_MAP = {
    'cfm': {'default': torch.nn.MSELoss},
    'ot': {'default': torch.nn.MSELoss},
    'vfm': {
        'MSE': torch.nn.MSELoss,
        'SSM': lambda: None,  # Placeholder for SSM loss
        'Gaussian': torch.nn.GaussianNLLLoss
    }
}


def get_args():
    """Parse and return command-line arguments."""
    parser = argparse.ArgumentParser(description='Two Moon Experiment')
    parser.add_argument('--model_type', default='vfm', type=str, help="Type of model: 'cfm', 'ot', 'vfm'")
    parser.add_argument('--device', default=0, type=int, help="CUDA device number, -1 for CPU")
    parser.add_argument('--num_epochs', default=5, type=int, help="Number of training epochs")
    parser.add_argument('--batch_size', default=128, type=int, help="Training batch size")
    parser.add_argument('--vfm_loss', default='MSE', type=str, help="Loss function for VFM: 'MSE', 'SSM', or 'Gaussian'")
    parser.add_argument('--learn_sigma', default=False, help="Flag to learn sigma in VFM")
    parser.add_argument('--integration_steps', default=100, type=int, help="Number of steps for integration in trajectory plotting")
    parser.add_argument('--dim', default=2, type=int, help="Dimensionality of the model input")
    parser.add_argument('--sigma', default=0.1, type=float, help="Sigma parameter for flow model")
    parser.add_argument('--save_model', action='store_true', help="Flag to save the trained model")
    parser.add_argument('--verbose', default=False, help="Flag to print training progress")
    parser.add_argument('--dataset', default='MNIST', type=str, help="Dataset to train the model on")
    return parser.parse_args()


def main(args):

    TRAINING_MAP = {
        'two_moons': train_two_moons,
        'MNIST': train_MNIST
    }

    # Check if model_type is valid
    assert args.model_type in MODEL_MAP[args.dataset], "Model type incorrect or not implemented."
    if args.vfm_loss == "MSE":
        assert args.learn_sigma == False, "Learning sigma is only available with loglikelihood loss in VFM."
    
    device = torch.device("cuda" if torch.cuda.is_available() and args.device >= 0 else "cpu")

    if args.model_type in ['cfm', 'ot']:
        savedir = os.path.join(os.getcwd(), f"results/{args.dataset}/{args.model_type}")
    else:
        savedir = os.path.join(os.getcwd(), f"results/{args.dataset}/{args.model_type}_{args.vfm_loss}_{'learned' if args.learn_sigma else 'fixed'}")

    Path(savedir).mkdir(parents=True, exist_ok=True)

    print(f"Training {args.model_type} model with the following configuration:")
    if args.model_type == 'vfm':
        print(f"Using loss function: {args.vfm_loss}", ("with fixed sigma" if args.learn_sigma == False else "with learned sigma") if args.vfm_loss != "MSE" else "")
    print(f"Device: {device}, Epochs: {args.num_epochs}, Batch Size: {args.batch_size}, Sigma: {args.sigma}, Dataset: {args.dataset}")  

    # Initialize models and criteria using dictionary mapping
    if args.dataset == 'two_moons':
        if args.model_type == 'vfm':
            flow_model = MODEL_MAP[args.dataset][args.model_type][args.learn_sigma]['flow'](args.sigma, args.learn_sigma)
            model = MODEL_MAP[args.dataset][args.model_type][args.learn_sigma]['model'](args.dim, time_varying=True).to(device)
        else:
            flow_model = MODEL_MAP[args.dataset][args.model_type][args.learn_sigma]['flow'](args.sigma)
            model = MODEL_MAP[args.dataset][args.model_type][args.learn_sigma]['model'](args.dim, time_varying=True).to(device)
    else:
        if args.model_type == 'vfm':
            flow_model = MODEL_MAP[args.dataset][args.model_type][args.learn_sigma]['flow'](args.sigma, args.learn_sigma)
            model = MODEL_MAP[args.dataset][args.model_type][args.learn_sigma]['model'](dim=(1, 28, 28), num_channels=32, num_res_blocks=1, num_classes=10, class_cond=True).to(device)
        else:
            flow_model = MODEL_MAP[args.dataset][args.model_type][args.learn_sigma]['flow'](args.sigma)
            model = MODEL_MAP[args.dataset][args.model_type][args.learn_sigma]['model'](dim=(1, 28, 28), num_channels=32, num_res_blocks=1, num_classes=10, class_cond=True).to(device)

    criterion = CRITERION_MAP[args.model_type][args.vfm_loss if args.model_type == 'vfm' else 'default']()

    optimizer = torch.optim.Adam(model.parameters())
    TRAINING_MAP[args.dataset](args, model, flow_model, criterion, optimizer, device, savedir, args.verbose)

    if args.save_model:
        torch.save(model.state_dict(), f"{savedir}/model.pt")


def train_two_moons(args, model, flow_model, criterion, optimizer, device, savedir, verbose):
    """Train the model based on the given configuration."""
    if verbose:
        start = time.time()

    for epoch in tqdm(range(args.num_epochs)):
        optimizer.zero_grad()
        x_0 = sample_8gaussians(args.batch_size)
        x_1 = sample_moons(args.batch_size)
        t, x_t, u_t = flow_model.sample_location_and_conditional_flow(x_0, x_1)

        if args.model_type in ['cfm', 'ot']:
            u_t = u_t.to(device)

        x_t = x_t.to(device)
        x_1 = x_1.to(device)
        t = t.to(device)

        if args.model_type in ['cfm', 'ot']:
            v_t = model(x_t, t)
            loss = criterion(v_t, u_t)
        else:  # VFM model
            if args.vfm_loss == 'MSE':
                mu_theta = model(x_t, t)
                loss = criterion(mu_theta, x_1)
            else:
                if args.learn_sigma:
                    mu_theta, sigma_theta = model(x_t, t)
                else:
                    mu_theta = model(x_t, t)
                    sigma_theta = torch.tensor(args.sigma).to(device)
                loss = criterion(mu_theta, x_1, sigma_theta.repeat(x_1.shape[0], 1))

        loss.backward()
        optimizer.step()

        if (epoch + 1) % 1000 == 0:
            end = time.time()

            if verbose:
                print(f"{epoch+1}: loss {loss.item():0.3f} time {(end - start):0.2f}")

            start = end
            traj = flow_model.integration(model, sample_8gaussians(1024), steps=args.integration_steps, device=device)
            plot_trajectories(traj=traj, output=f"{savedir}/iter_{epoch+1}.png")

def train_MNIST(args, model, flow_model, criterion, optimizer, device, savedir, verbose):

    trainset = datasets.MNIST(
        "data",
        train=True,
        download=True,
        transform=transforms.Compose([transforms.ToTensor(), transforms.Normalize((0.5,), (0.5,))]),
    )

    train_loader = torch.utils.data.DataLoader(
        trainset, batch_size=args.batch_size, shuffle=True, drop_last=True
    )

    pbar = tqdm(total=args.num_epochs * len(train_loader))

    for epoch in range(args.num_epochs):
        for i, data in enumerate(train_loader):

            optimizer.zero_grad()

            x1 = data[0].to(device)
            y = data[1].to(device)
            x0 = torch.randn_like(x1)

            t, xt, ut = flow_model.sample_location_and_conditional_flow(x0, x1)
            vt = model(xt, t, y)
            loss = criterion(vt, ut)

            loss.backward()
            optimizer.step()

            if verbose:
                print(f"epoch: {epoch}, steps: {i}, loss: {loss.item():.4}", end="\r")

            pbar.update(1)
    
    pbar.close()

    generated_class_list = torch.arange(10, device=device).repeat(10)

    traj = flow_model.integration(
        model=model, 
        x_0=torch.randn(100, 1, 28, 28, device=device),
        steps=args.integration_steps,
        device=device,
        y=generated_class_list
    )

    grid = make_grid(
        traj[-1, :100].view([-1, 1, 28, 28]).clip(-1, 1), value_range=(-1, 1), padding=0, nrow=10
    )
    img = ToPILImage()(grid)
    plt.imshow(img)
    plt.savefig(f"{savedir}/final.png")


if __name__ == "__main__":
    args = get_args()
    main(args)