#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Jan 18 2025

@author: Liam Yih
Updated: Jan 29 2026 - Ported functions from Julia version and fixed inconsistencies
"""


import pandas as pd
import numpy as np

TOL = 1e-10  # Updated to match Julia version
GRADIENT = 0.5
PARAMETERS = pd.Series({
    'S0':       50,        # Price at tender offer, updated until offer is accepted
    'x0':       1e6,       # Initial stock quantity (updated to match Julia: 1e6 not 1e3)
    'T':        5,         # Make sure T is an integer (units in ticks) - Chosen
    'tau':      1,         # Make sure that tau is an integer and a perfect divisor of T - Chosen
    'sigma':    0.95,      # ** Volatility ---> Collected from track data
    'alpha':    0.02,      # ** Drift --------> Collected from track data
    'epsilon':  0.0625,    # Cacluated as half of bid-ask spread
    'gamma':    2.5e-7,    # ** Permanent price impact. Rule of thumb: One bid-ask spread per 10% of daily volume
    'eta':      2.5e-6,    # ** Temporary price impact. Rule of thumb: One bid-ask spread per 1% of daily volume
});


# def calculate_parameters(p):
#     '''
#     Idea for this function is that it starts when the tender offer comes through is then
#     updated every tick until it is accepted. The parameters are locked in when the tender
#     is accepted.
#     
#     I don't know what you've called your variables so I leave that to you
#     p = List of parameters (like PARAMETERS at the top of this script)
#     '''
#     p['S0'] = # current_price
#     p['x0'] = # Quantity of assests on the tender offer
#     p['epsilon'] = # ask - bid
#     p['lambda_u'] = # Function I have yet to write
    


def calculate_kappa(p, lambda_u, max_iterations=100):
    """
    Calculate kappa using Newton's method.
    Updated to match Julia version with improved initial guess handling.
    """
    if lambda_u == 0:
        return 0.0
    
    # Use Newton's method as a root-finding algorithm to determine kappa
    # from the equation after eq. 16 in the paper
    dt = p['tau']
    k2 = lambda_u * p['sigma']**2 / (p['eta'] - 0.5 * p['gamma'] * dt)
    
    # Improved initial guess (matches Julia)
    kappa = np.sign(k2) * np.sqrt(np.abs(k2))
    test = 2 * (np.cosh(kappa * dt) - 1) - k2 * dt**2
    n = 0
    
    while (abs(test) > TOL and n < max_iterations):
        kappa -= (np.cosh(kappa * dt) - 1 - 0.5 * dt**2 * k2) / (dt * np.sinh(kappa * dt))
        test = 2 * (np.cosh(kappa * dt) - 1) - k2 * dt**2
        n += 1
        
    if n == max_iterations:
        raise ValueError(f"kappa finder timed out with error {test}")
        
    return kappa


def optimal_statistics(p, k):
    """
    Calculate expected cost and variance of cost.
    This function is consistent between Python and Julia versions.
    """
    g = p['gamma']
    dt = p['tau']
    x0 = p['x0']
    eta = p['eta'] - g * dt / 2
    T = p['T']
    eps = p['epsilon']
    s = p['sigma']
    
    expected_cost = (
        0.5 * g * x0**2 + eps * x0 + 
        eta * x0**2 * np.tanh(k * dt / 2) * (dt * np.sinh(2 * k * T) + 2 * T * np.sinh(k * dt)) / 
        (2 * dt**2 * np.sinh(k * T)**2)
    )
        
    variance_of_cost = (
        0.5 * s**2 * x0**2 *
        (dt * np.sinh(k * T) * np.cosh(k * (T - dt)) - T * np.sinh(k * dt)) / 
        (np.sinh(k * T)**2 * np.sinh(k * dt))
    )
    
    return expected_cost, variance_of_cost


def calculate_gradient(a, b, p):
    """
    Calculate gradient between two lambda values.
    FIXED: Now uses sqrt(V) as in Julia version, not V directly.
    """
    E_prev, V_prev = optimal_statistics(p, calculate_kappa(p, a))
    E, V = optimal_statistics(p, calculate_kappa(p, b))
    
    # CRITICAL FIX: Use sqrt(V) not V
    return (E - E_prev) / (np.sqrt(V) - np.sqrt(V_prev))


def gradient_bisection(a, b, p, tol=1e-10, max_itr=1000):
    """
    Use bisection to find lambda where gradient equals -GRADIENT.
    Ported from Julia version.
    """
    itr = 0
    mid = (a + b) / 2
    check = GRADIENT + calculate_gradient(a, mid, p)
    error = abs(check)
    
    while (error > TOL and itr < max_itr):
        itr += 1
        
        if check > 0:
            a = mid
        elif check < 0:
            b = mid
        else:
            return mid
        
        mid = (a + b) / 2
        check = GRADIENT + calculate_gradient(a, b, p)
        error = abs(check)
    
    print(f"Bisection completed with error of {error} and {itr} iterations")
    return (a + b) / 2


def find_lambda(p):
    """
    Find optimal lambda_u value.
    FIXED: Now uses sqrt(V) as in Julia version.
    Ported from Julia version with corrections.
    """
    # Start with very small lambda
    lambda_prev = 1e-12
    k_prev = calculate_kappa(p, lambda_prev)
    E_prev, V_prev = optimal_statistics(p, k_prev)
    
    # Double lambda
    lambda_u = 2 * lambda_prev
    k = calculate_kappa(p, lambda_u)
    E, V = optimal_statistics(p, k)
    
    # CRITICAL FIX: Use sqrt(V) not V
    gradient = (E - E_prev) / (np.sqrt(V) - np.sqrt(V_prev))
    
    # Search until gradient magnitude exceeds GRADIENT (while gradient is negative)
    while (abs(gradient) < GRADIENT and gradient < 0):
        lambda_prev = lambda_u
        E_prev = E
        V_prev = V
        
        lambda_u *= 2
        k = calculate_kappa(p, lambda_u)
        E, V = optimal_statistics(p, k)
        
        gradient = (E - E_prev) / (np.sqrt(V) - np.sqrt(V_prev))
    
    if gradient > 0:
        raise ValueError("Positive gradient encountered")
    
    # Use bisection to refine
    sol = gradient_bisection(lambda_prev, lambda_u, p)
    
    return sol


def calculate_lambda_u_old(p):
    """
    OLD VERSION - kept for reference.
    This version had the bug of using V instead of sqrt(V).
    """
    # First begin by finding the minimum at lambda = 0
    lambda_u_prev = 1e-16
    k_prev = calculate_kappa(p, lambda_u_prev)
    E_prev, V_prev = optimal_statistics(p, k_prev)
    
    lambda_u = 1e-15
    k = calculate_kappa(p, lambda_u)
    E, V = optimal_statistics(p, k)
    
    gradient = (E - E_prev) / (V - V_prev)  # BUG: Should be sqrt(V)
    print(gradient)
    
    # Note that C is positive and gradient will be negative
    while (gradient > -GRADIENT and gradient < 0):
        lambda_u_prev = lambda_u
        print(lambda_u)
        k_prev = k
        E_prev = E
        V_prev = V
    
        lambda_u *= 10
        k = calculate_kappa(p, lambda_u)
        E, V = optimal_statistics(p, k)
        
        gradient = (E - E_prev) / (V - V_prev)  # BUG: Should be sqrt(V)
        
    if gradient > 0:
        raise ValueError('Lambda searcher gradient was positive.')
        
    print(lambda_u, lambda_u_prev)
    print(gradient)


def print_schedule(p, k, tick):
    """
    Generate trading schedule.
    This function is consistent between Python and Julia versions.
    """
    dt = p['tau']
    T = p['T']
    N = int(T / dt)
    
    t_half_list = (np.arange(1, N + 1, dtype=float) - 0.5) * dt
    sale_times = tick + (np.arange(1, N + 1, dtype=float) - 1) * dt
    
    denom = np.sinh(k * T)
    
    prefactor = 2.0 * np.sinh(k * dt) / denom
    number_to_sell = prefactor * np.cosh(k * (T - t_half_list)) * p['x0']
    number_to_sell = np.round(number_to_sell)
    
    for n in range(N):
        print(f"Trade {number_to_sell[n]:.0f} at {sale_times[n]}")
    
    trade_schedule = pd.DataFrame({
        "sale_time": sale_times,
        "number_to_sell": number_to_sell
    })
    
    return trade_schedule


def main():
    """
    Main function demonstrating usage of the updated functions.
    """
    print("=" * 60)
    print("Trade Optimiser - Updated Version")
    print("=" * 60)
    
    # Find optimal lambda
    print("\nFinding optimal lambda_u...")
    lambda_opt = find_lambda(PARAMETERS)
    print(f"Optimal lambda_u: {lambda_opt:.6e}")
    
    # Calculate optimal kappa
    k_opt = calculate_kappa(PARAMETERS, lambda_opt)
    print(f"Optimal kappa: {k_opt:.6f}")
    
    # Calculate statistics
    E, V = optimal_statistics(PARAMETERS, k_opt)
    print(f"\nExpected cost: {E:.2f}")
    print(f"Variance of cost: {V:.2f}")
    print(f"Standard deviation: {np.sqrt(V):.2f}")
    
    # Calculate gradient at optimal point
    gradient = calculate_gradient(lambda_opt - 1e-8, lambda_opt + 1e-8, PARAMETERS)
    print(f"Gradient at optimal point: {gradient:.6f} (target: {-GRADIENT:.6f})")
    
    # Generate and print trading schedule
    print("\n" + "=" * 60)
    print("Trading Schedule")
    print("=" * 60)
    schedule = print_schedule(PARAMETERS, k_opt, tick=1)
    
    return schedule


if __name__ == '__main__':
    main()
