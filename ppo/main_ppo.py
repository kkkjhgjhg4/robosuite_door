# main_ppo.py

import os
from torch.utils.tensorboard import SummaryWriter
import robosuite as suite
from robosuite.wrappers import GymWrapper
from ppo_torch import PPOAgent

if __name__ == '__main__':

    # Create directory for PPO models if it doesn't exist
    if not os.path.exists("tmp/ppo"):
        os.makedirs("tmp/ppo")
    else:
        pass  # No action needed if directory exists

    # Define environment
    env_name = "Door"

    env = suite.make(
        env_name,
        robots=["Panda"],
        controller_configs=suite.load_controller_config(default_controller="JOINT_VELOCITY"),
        has_renderer=False,
        use_camera_obs=False,
        horizon=300,
        reward_shaping=True,
        control_freq=20,
    )

    env = GymWrapper(env)

    # Hyperparameters
    lr_actor = 3e-4
    lr_critic = 1e-3
    gamma = 0.99
    K_epochs = 80
    eps_clip = 0.2
    buffer_size = 2048
    batch_size = 64
    entropy_coeff = 0.01

    input_dims = env.observation_space.shape
    n_actions = env.action_space.shape[0]

    # Initialize PPO agent
    agent = PPOAgent(
        input_dims=input_dims,
        n_actions=n_actions,
        lr_actor=lr_actor,
        lr_critic=lr_critic,
        gamma=gamma,
        K_epochs=K_epochs,
        eps_clip=eps_clip,
        buffer_size=buffer_size,
        batch_size=batch_size,
        entropy_coeff=entropy_coeff,
        device=torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
    )

    # Initialize TensorBoard writer
    writer = SummaryWriter('logs/ppo')

    # Number of training episodes
    n_games = 10000

    # Episode identifier for logging
    episode_identifier = f"PPO - lr_actor={lr_actor} lr_critic={lr_critic} K_epochs={K_epochs} eps_clip={eps_clip}"

    # Load existing models if available
    agent.load_models()

    for i in range(n_games):
        state, _ = env.reset()
        done = False
        score = 0

        while not done:
            action, log_prob = agent.choose_action(state)
            next_state, reward, done, extra, info = env.step(action)
            score += reward
            agent.remember(state, action, log_prob, reward, done)
            state = next_state

        # Update PPO agent after each episode
        agent.update()

        # Log the score
        writer.add_scalar(f"Score - {episode_identifier}", score, global_step=i)

        # Save models every 10 episodes
        if i % 10 == 0:
            agent.save_models()

        print(f"Episode: {i} Score: {score}")

    writer.close()